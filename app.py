from flask import Flask, request
import requests
import re
from bs4 import BeautifulSoup
import os
import time
import random

app = Flask(__name__)

VERIFY_TOKEN = 'my_verify_token'
PAGE_ACCESS_TOKEN = 'your_page_access_token'
IG_BUSINESS_ID = 'your_ig_business_account_id'

# Simple in-memory user message timestamps (user_id -> list of timestamps)
user_message_times = {}
MAX_MESSAGES_PER_HOUR = 5

# In-memory whitelist for users who confirmed follow
followers_whitelist = set()

def can_send_message(user_id):
    now = time.time()
    window = 3600  # seconds (1 hour)
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
    match = re.search(r"(https?://(?:www\.)?instagram\.com/reel/[^\s/?]+)", text)
    return match.group(1) if match else None

def download_reels(reels_url):
    headers = {'User-Agent': 'Mozilla/5.0'}
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
    return None

def upload_to_transfersh(file_path):
    with open(file_path, 'rb') as f:
        res = requests.put('https://transfer.sh/reel.mp4', data=f)
    if res.status_code == 200:
        return res.text.strip()
    return None

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
                if "message" in messaging:
                    text = messaging["message"].get("text")
                    if not text:
                        return "OK", 200

                    # Rate limit check
                    if not can_send_message(sender_id):
                        print(f"Rate limit hit for user {sender_id}")
                        return "OK", 200

                    # Whitelist check
                    if sender_id not in followers_whitelist:
                        if text.lower() in ['i follow', 'followed', 'done']:
                            followers_whitelist.add(sender_id)
                            send_reply(sender_id, "Thanks for following! Send me an Instagram Reels link to download.")
                        else:
                            send_reply(sender_id, "Please follow me on Instagram, then reply with 'I follow' to start using this bot.")
                        return "OK", 200

                    # Process reels
                    reels_url = extract_reels_url(text)
                    if reels_url:
                        filepath = download_reels(reels_url)
                        if filepath:
                            download_link = upload_to_transfersh(filepath)
                            if download_link:
                                send_reply(sender_id, f"🎥 Here's your video: {download_link}")
                                os.remove(filepath)
                            else:
                                send_reply(sender_id, "⚠️ Failed to upload video.")
                        else:
                            send_reply(sender_id, "⚠️ Couldn’t download the video.")
                    else:
                        send_reply(sender_id, "Send me an Instagram Reels link to download.")
    return "OK", 200

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000)
