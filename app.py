import os
import json
import requests
import uuid
from datetime import datetime
from flask import Flask, request, jsonify, render_template
from flask_cors import CORS
from google import genai
from google.genai import types
from dotenv import load_dotenv

# Load env variables from .env relative to the script's directory
basedir = os.path.abspath(os.path.dirname(__file__))
load_dotenv(os.path.join(basedir, '.env'), override=True)

app = Flask(__name__)
# Enable CORS for cross-origin requests
CORS(app)

# Initialize the Gemini Client
api_key = os.environ.get("GEMINI_API_KEY")
client = None

if api_key and api_key != "your_actual_gemini_api_key_here":
    # Google GenAI client automatically uses the GEMINI_API_KEY environment variable
    client = genai.Client()
else:
    print(f"WARNING: GEMINI_API_KEY is not configured or is set to placeholder. Value: {api_key!r}")

@app.route('/')
def index():
    return render_template('index.html')

def load_history_data():
    history_file = os.path.join(basedir, 'history.json')
    if os.path.exists(history_file):
        try:
            with open(history_file, 'r', encoding='utf-8') as f:
                data = json.load(f)
                if isinstance(data, list):
                    # Migrate old list format to new multi-session format
                    default_id = "legacy_session"
                    return {
                        "sessions": {
                            default_id: {
                                "id": default_id,
                                "title": "Imported History",
                                "created_at": datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
                                "messages": data
                            }
                        }
                    }
                elif isinstance(data, dict) and "sessions" in data:
                    return data
        except Exception as e:
            print(f"Error loading history: {e}")
    return {"sessions": {}}

def save_history_data(data):
    history_file = os.path.join(basedir, 'history.json')
    try:
        with open(history_file, 'w', encoding='utf-8') as f:
            json.dump(data, f, indent=4)
    except Exception as e:
        print(f"Error saving history: {e}")

@app.route('/sessions', methods=['GET'])
def get_sessions():
    data = load_history_data()
    sessions_list = []
    for sid, info in data.get("sessions", {}).items():
        sessions_list.append({
            "id": sid,
            "title": info.get("title", "Untitled Chat"),
            "created_at": info.get("created_at", "")
        })
    sessions_list.sort(key=lambda x: x.get("created_at", ""), reverse=True)
    return jsonify(sessions_list)

@app.route('/sessions', methods=['POST'])
def create_session():
    data = load_history_data()
    new_id = str(uuid.uuid4())
    new_session = {
        "id": new_id,
        "title": "New Chat",
        "created_at": datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
        "messages": []
    }
    data["sessions"][new_id] = new_session
    save_history_data(data)
    return jsonify(new_session)

@app.route('/sessions/<session_id>', methods=['GET'])
def get_session(session_id):
    data = load_history_data()
    session = data.get("sessions", {}).get(session_id)
    if not session:
        return jsonify({"error": "Session not found"}), 404
    return jsonify(session)

@app.route('/sessions/<session_id>', methods=['DELETE'])
def delete_session(session_id):
    data = load_history_data()
    if session_id in data.get("sessions", {}):
        del data["sessions"][session_id]
        save_history_data(data)
        return jsonify({"status": "deleted", "id": session_id})
    return jsonify({"error": "Session not found"}), 404

@app.route('/history', methods=['DELETE'])
def clear_history():
    history_file = os.path.join(basedir, 'history.json')
    if os.path.exists(history_file):
        try:
            os.remove(history_file)
            return jsonify({'status': 'cleared'})
        except Exception as e:
            return jsonify({'error': str(e)}), 500
    return jsonify({'status': 'already_empty'})

@app.route('/ask', methods=['POST'])
def ask():
    global client
    print(f"DEBUG /ask: Initial client state: {client}")
    # Re-initialize client if key was configured after startup
    if client is None:
        load_dotenv(os.path.join(basedir, '.env'), override=True)
        key = os.environ.get("GEMINI_API_KEY")
        print(f"DEBUG /ask: Loaded GEMINI_API_KEY: {key!r}")
        if key and key != "your_actual_gemini_api_key_here":
            os.environ["GEMINI_API_KEY"] = key
            client = genai.Client()
            print(f"DEBUG /ask: Client initialized: {client}")
        else:
            print("DEBUG /ask: Client not initialized because key was empty or placeholder")

    print(f"DEBUG /ask: Final client state: {client}")

    if client is None:
        return jsonify({
            'error': 'API Key Not Configured. Please set your GEMINI_API_KEY in the voice-assistant/.env file.'
        }), 500

    try:
        data = request.get_json()
        if not data or 'message' not in data:
            return jsonify({'error': 'No message provided in request.'}), 400

        user_message = data['message']
        local_time = data.get('local_time')
        timezone = data.get('timezone')
        session_id = data.get('session_id')

        # Load existing sessions
        history_data = load_history_data()
        
        # Ensure session exists
        if not session_id or session_id not in history_data.get("sessions", {}):
            session_id = str(uuid.uuid4())
            history_data["sessions"][session_id] = {
                "id": session_id,
                "title": "New Chat",
                "created_at": datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
                "messages": []
            }

        session = history_data["sessions"][session_id]

        # Generate a dynamic title if it is default
        if not session["messages"] or session["title"] == "New Chat":
            title = user_message.strip()
            if len(title) > 35:
                title = title[:32] + "..."
            session["title"] = title

        system_instruction = (
            "You are Sirivela, a friendly, helpful, and concise conversational voice assistant and chatbot. "
            "Keep your answers brief (1-3 sentences) so they sound natural when read aloud. "
            "Do not use markdown formatting (such as **, _, or bullet lists) in your response, "
            "as they interfere with smooth speech synthesis. Speak naturally. "
            "CRITICAL: For almost every informational response, you must automatically identify the main subject or entity being discussed (e.g. a specific car, animal, place, food, or object) and append '[IMAGE: subject]' at the very end of your response text (where 'subject' is the identified keyword, e.g. '[IMAGE: lion]' or '[IMAGE: electric car]'). This allows the interface to automatically display a relevant picture alongside the info."
        )

        if local_time:
            system_instruction += (
                f"\n\nUser's current local time context:\n"
                f"- Local Time: {local_time}\n"
                f"- Timezone: {timezone}\n"
                f"Refer to this local time instead of UTC or any other reference if the user asks about the time, date, day of the week, or relative time references (like today, yesterday, tomorrow, etc.)."
            )

        # Call Gemini API
        # Model: gemini-2.5-flash (optimized for speed and low latency, has free tier)
        response = client.models.generate_content(
            model='gemini-2.5-flash',
            contents=user_message,
            config=types.GenerateContentConfig(
                system_instruction=system_instruction,
                tools=[types.Tool(google_search=types.GoogleSearch())],
                max_output_tokens=1000,
            )
        )

        assistant_text = response.text

        # Append messages to session
        session["messages"].append({
            'sender': 'user',
            'text': user_message,
            'timestamp': datetime.now().strftime('%H:%M:%S')
        })
        session["messages"].append({
            'sender': 'assistant',
            'text': assistant_text,
            'timestamp': datetime.now().strftime('%H:%M:%S')
        })

        save_history_data(history_data)

        return jsonify({
            'response': assistant_text,
            'session_id': session_id,
            'session_title': session["title"]
        })
    except Exception as e:
        print(f"Error calling Gemini API: {e}")
        return jsonify({'error': f"Gemini API error: {str(e)}"}), 500

@app.route('/image', methods=['GET'])
def get_image():
    query = request.args.get('q')
    if not query:
        return jsonify({'error': 'No query provided'}), 400

    headers = {'User-Agent': 'SirivelaVoiceAssistant/1.0 (contact@example.com)'}
    fallback_url = f"https://loremflickr.com/600/400/{query}"
    fallback_images = [
        {
            'url': fallback_url,
            'source': 'loremflickr.com',
            'link': 'https://loremflickr.com'
        }
    ]

    try:
        # Query Wikimedia Commons for up to 3 image results
        search_url = f"https://commons.wikimedia.org/w/api.php?action=query&generator=search&gsrsearch={query}&gsrnamespace=6&prop=imageinfo&iiprop=url&format=json&gsrlimit=3"
        r = requests.get(search_url, headers=headers, timeout=5)
        data = r.json()
        pages = data.get('query', {}).get('pages', {})
        
        images = []
        if pages:
            # Sort pages by match index
            sorted_pages = sorted(pages.values(), key=lambda x: x.get('index', 0))
            for page in sorted_pages:
                info = page.get('imageinfo', [])
                if info:
                    url = info[0].get('url')
                    desc_url = info[0].get('descriptionurl', '')
                    if url:
                        images.append({
                            'url': url,
                            'source': 'commons.wikimedia.org',
                            'link': desc_url
                        })
            
            if images:
                return jsonify({
                    'url': images[0]['url'],
                    'images': images
                })
    except Exception as e:
        print(f"Error fetching Wikimedia Commons images for {query}: {e}")
        
    return jsonify({
        'url': fallback_url,
        'images': fallback_images
    })

if __name__ == '__main__':
    # Run the server on all network interfaces
    app.run(debug=True, host='0.0.0.0', port=5000)
