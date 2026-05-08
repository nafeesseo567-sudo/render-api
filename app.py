import os, json, subprocess, re
from flask import Flask, request, jsonify

app = Flask(__name__)

ALLOWED_QUALITIES = {"best", "1080p", "720p", "480p", "360p", "audio"}

def quality_to_format(q):
    return {
        "1080p": "bestvideo[height<=1080][ext=mp4]+bestaudio[ext=m4a]/best[height<=1080][ext=mp4]/best",
        "720p":  "bestvideo[height<=720][ext=mp4]+bestaudio[ext=m4a]/best[height<=720][ext=mp4]/best",
        "480p":  "bestvideo[height<=480][ext=mp4]+bestaudio[ext=m4a]/best[height<=480][ext=mp4]/best",
        "360p":  "bestvideo[height<=360][ext=mp4]+bestaudio[ext=m4a]/best[height<=360][ext=mp4]/best",
        "audio": "bestaudio[ext=m4a]/bestaudio",
    }.get(q, "bestvideo[ext=mp4]+bestaudio[ext=m4a]/best[ext=mp4]/best")

def fmt_count(n):
    if n is None: return "N/A"
    if n >= 1_000_000: return f"{n/1_000_000:.1f}M"
    if n >= 1_000:     return f"{n/1_000:.1f}K"
    return str(n)

def fmt_duration(s):
    if not s: return "Unknown"
    h, rem = divmod(int(s), 3600)
    m, sec = divmod(rem, 60)
    return f"{h}:{m:02d}:{sec:02d}" if h else f"{m}:{sec:02d}"

def friendly_error(text):
    t = text.lower()
    if "private"     in t: return "This video is private or restricted."
    if "unavailable" in t: return "Video is unavailable or has been removed."
    if "copyright"   in t: return "Video blocked due to copyright."
    if "age"         in t: return "Age-restricted content."
    if "unsupported" in t: return "This URL is not supported."
    return f"yt-dlp error: {text[:300]}"

@app.after_request
def add_cors(resp):
    allowed = os.environ.get("ALLOWED_ORIGINS", "*")
    origin  = request.headers.get("Origin", "")
    if allowed == "*" or origin in allowed.split(","):
        resp.headers["Access-Control-Allow-Origin"]  = allowed if allowed == "*" else origin
    resp.headers["Access-Control-Allow-Methods"] = "GET, POST, OPTIONS"
    resp.headers["Access-Control-Allow-Headers"] = "Content-Type, X-WP-Nonce"
    return resp

@app.route("/api/health")
def health():
    try:
        v = subprocess.check_output(["yt-dlp", "--version"], timeout=5).decode().strip()
    except Exception as e:
        v = f"error: {e}"
    return jsonify({"status": "ok", "version": "3.0.0", "ytdlp": v})

@app.route("/api/download", methods=["POST", "OPTIONS"])
def download():
    if request.method == "OPTIONS":
        return "", 204

    data    = request.get_json(silent=True) or {}
    url     = data.get("url", "").strip()
    quality = data.get("quality", "best")

    if not url or not url.startswith("http"):
        return jsonify({"error": "Missing or invalid URL."}), 400

    if quality not in ALLOWED_QUALITIES:
        quality = "best"

    is_yt = "youtube.com" in url or "youtu.be" in url
    is_ig = "instagram.com" in url

    if not is_yt and not is_ig:
        return jsonify({"error": "Only YouTube and Instagram URLs are supported."}), 400

    fmt = quality_to_format(quality)
    cmd = [
        "yt-dlp",
        "--no-warnings", "--quiet", "--dump-json", "--no-playlist",
        "--format", fmt,
        "--user-agent", "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
        "--add-header", "Referer:https://www.google.com/",
        url,
    ]

    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=60)
    except subprocess.TimeoutExpired:
        return jsonify({"error": "Request timed out. Try a shorter video."}), 504
    except FileNotFoundError:
        return jsonify({"error": "yt-dlp is not installed on this server."}), 503

    if result.returncode != 0:
        return jsonify({"error": friendly_error(result.stderr or result.stdout)}), 422

    info = None
    for line in reversed((result.stdout or "").splitlines()):
        line = line.strip()
        if line.startswith("{"):
            try:
                info = json.loads(line)
                break
            except json.JSONDecodeError:
                continue

    if not info:
        return jsonify({"error": "Failed to parse yt-dlp output."}), 500

    # Resolve video URL
    video_url = info.get("url", "")
    if not video_url:
        for f in info.get("requested_formats", []):
            if f.get("vcodec", "none") != "none" and f.get("url"):
                video_url = f["url"]
                break
    if not video_url:
        mp4s = [f for f in info.get("formats", []) if f.get("ext") == "mp4" and f.get("url")]
        if mp4s:
            video_url = mp4s[-1]["url"]

    if not video_url:
        return jsonify({"error": "yt-dlp did not return a usable video URL."}), 500

    platform = "youtube" if is_yt else "instagram"

    return jsonify({
        "video_url": video_url,
        "platform":  platform,
        "metadata": {
            "title":          info.get("title", "Video")[:200],
            "description":    (info.get("description") or "")[:200],
            "hashtags":       (info.get("tags") or [])[:10],
            "estimatedLikes": fmt_count(info.get("like_count")),
            "viewCount":      fmt_count(info.get("view_count")),
            "duration":       fmt_duration(info.get("duration")),
            "uploader":       info.get("uploader") or info.get("channel") or "Unknown",
            "quality":        f"{info.get('width','?')}x{info.get('height','?')}",
            "thumbnail":      info.get("thumbnail", ""),
            "platform":       platform,
        },
    })

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)
