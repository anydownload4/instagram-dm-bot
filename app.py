import os
import time
import re
import requests
from flask import Flask, request
from bs4 import BeautifulSoup

app = Flask(__name__)

# Use environment variables for security
VERIFY_TOKEN = os.getenv("VERIFY_TOKEN", "your_verify_token")
PAGE_ACCESS_TOKEN = os.getenv("PAGE_ACCESS_TOKEN", "your_page_access_token")

# Rate limit: user_id → list of timestamps
user_message_times = {}
MAX_MESSAGES_PER_HOUR = 5

# Simple in-memory follower whitelist
followers_whitelist = set()

# === Helper Functions ===

def can_send_message(user_id):
    now = time.time()
    window = 3600
    timestamps = user_message_times.get(user_id, [])
    timestamps = [t for t in timestamps if now - t < window]
    if len(timestamps) < MAX_MESSAGES_PER_HOUR:
        timestamps.append(now)
        user_message_times[user_id] = timestamps
        return True
    return False

def send_reply(recipient_id, message_text):
    url = f"https://graph.facebook.com/v18.0/me/messages?access_token={PAGE_ACCESS_TOKEN}"
    headers = {"Content-Type": "application/json"}
    payload = {
        "recipient": {"id": recipient_id},
        "message": {"text": message_text}
    }
    res = requests.post(url, headers=headers, json=payload)
    print(f"Sent reply to {recipient_id}: {res.status_code} {res.text}")

def extract_reels_url(text):
    match = re.search(r"(https?://(?:www\.)?instagram\.com/reel/[^\s/?#&]+)", text)
    return match.group(1) if match else None

def download_reels(reels_url):
    headers = {'User-Agent': 'Mozilla/5.0'}
    try:
        res = requests.get(reels_url, headers=headers)
        if res.status_code != 200:
            return None

        soup = BeautifulSoup(res.text, 'html.parser')
        video_url = None
        for tag in soup.find_all('meta'):
            if tag.get('property') == 'og:video':
                video_url = tag.get('content')
                break

        if not video_url:
            return None

        video_data = requests.get(video_url, stream=True)
        if video_data.status_code == 200:
            with open("reel.mp4", 'wb') as f:
                for chunk in video_data.iter_content(1024):
                    f.write(chunk)
            return "reel.mp4"
    except Exception as e:
        print(f"Download failed: {e}")
    return None

def upload_to_transfersh(file_path):
    try:
        with open(file_path, 'rb') as f:
            res = requests.put('https://transfer.sh/reel.mp4', data=f)
        if res.status_code == 200:
            return res.text.strip()
    except Exception as e:
        print(f"Upload failed: {e}")
    return None

# === Webhook ===

@app.route('/webhook', methods=['GET'])
def verify():
    mode = request.args.get("hub.mode")
    token = request.args.get("hub.verify_token")
    challenge = request.args.get("hub.challenge")
    if mode == "subscribe" and token == VERIFY_TOKEN:
        return challenge, 200
    return "Verification failed", 403

@app.route('/webhook', methods=['POST'])
def webhook():
    data = request.get_json()
    if data.get("object") == "page":
        for entry in data.get("entry", []):
            for messaging in entry.get("messaging", []):
                sender_id = messaging["sender"]["id"]
                message = messaging.get("message", {})
                text = message.get("text")

                if not text:
                    continue

                # Rate limit
                if not can_send_message(sender_id):
                    send_reply(sender_id, "⏳ Please wait — you've hit the hourly limit.")
                    continue

                # Follower verification
                if sender_id not in followers_whitelist:
                    if text.lower().strip() in ['i follow', 'followed', 'done']:
                        followers_whitelist.add(sender_id)
                        send_reply(sender_id, "✅ Thanks for following! Now send a Reels link.")
                    else:
                        send_reply(sender_id, "👋 Please follow us on Instagram and reply 'I follow' to continue.")
                    continue

                # Extract and download Reels
                reels_url = extract_reels_url(text)
                if reels_url:
                    send_reply(sender_id, "🔄 Downloading your video...")
                    filepath = download_reels(reels_url)
                    if filepath:
                        download_link = upload_to_transfersh(filepath)
                        if download_link:
                            send_reply(sender_id, f"✅ Here's your download link:\n{download_link}")
                        else:
                            send_reply(sender_id, "⚠️ Failed to upload the video.")
                        os.remove(filepath)
                    else:
                        send_reply(sender_id, "⚠️ Could not download the video.")
                else:
                    send_reply(sender_id, "❌ Please send a valid Instagram Reels link.")
    return "OK", 200

# === Run app ===

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000)
