import os
import json
import requests
import uuid
import urllib.parse
import hashlib
from datetime import datetime
from flask import Flask, request, jsonify, render_template, session, redirect, url_for
from flask_cors import CORS
from google import genai
from google.genai import types
from dotenv import load_dotenv
import rag_helper

# Load env variables from .env relative to the script's directory
basedir = os.path.abspath(os.path.dirname(__file__))
load_dotenv(os.path.join(basedir, '.env'), override=True)

app = Flask(__name__)
# Enable CORS for cross-origin requests
CORS(app)

# Set secret key for Flask session signing
app.secret_key = os.environ.get("FLASK_SECRET_KEY", "sirivela-secret-key-123-change-me")

# Initialize the Gemini Client
api_key = os.environ.get("GEMINI_API_KEY")
client = None

if api_key and api_key != "your_actual_gemini_api_key_here":
    # Google GenAI client automatically uses the GEMINI_API_KEY environment variable
    client = genai.Client()
    try:
        rag_helper.init_rag(client)
    except Exception as e:
        print(f"RAG Error: Failed to initialize RAG on startup: {e}")
else:
    print(f"WARNING: GEMINI_API_KEY is not configured or is set to placeholder. Value: {api_key!r}")

# Users database helpers (JSON-based)
def load_users_data():
    users_file = os.path.join(basedir, 'users.json')
    if os.path.exists(users_file):
        try:
            with open(users_file, 'r', encoding='utf-8') as f:
                return json.load(f)
        except Exception as e:
            print(f"Error loading users: {e}")
    return {"users": {}}

def save_users_data(data):
    users_file = os.path.join(basedir, 'users.json')
    try:
        with open(users_file, 'w', encoding='utf-8') as f:
            json.dump(data, f, indent=4)
    except Exception as e:
        print(f"Error saving users: {e}")

def hash_password(password):
    return hashlib.sha256(password.encode()).hexdigest()

@app.route('/')
def index():
    google_client_id = os.environ.get("GOOGLE_CLIENT_ID", "").strip()
    if google_client_id.startswith("your_") or not google_client_id:
        google_client_id = ""
    return render_template('index.html', google_client_id=google_client_id)

@app.route('/auth/user', methods=['GET'])
def get_auth_user():
    user = session.get('user')
    if not user:
        return jsonify({"authenticated": False}), 401
    return jsonify({"authenticated": True, "user": user})

@app.route('/auth/login', methods=['GET'])
def auth_login():
    google_client_id = os.environ.get("GOOGLE_CLIENT_ID", "").strip()
    if google_client_id.startswith("your_") or not google_client_id:
        return "Google Client ID not configured", 400
        
    redirect_uri = request.host_url.rstrip('/') + '/auth/callback'
    # Force HTTPS when running on production (Render.com)
    if 'localhost' not in request.host and '127.0.0.1' not in request.host and '192.168.' not in request.host:
        if redirect_uri.startswith("http://"):
            redirect_uri = redirect_uri.replace("http://", "https://", 1)
            
    params = {
        "client_id": google_client_id,
        "redirect_uri": redirect_uri,
        "response_type": "code",
        "scope": "openid email profile",
        "access_type": "offline",
        "prompt": "select_account"
    }
    auth_url = "https://accounts.google.com/o/oauth2/v2/auth?" + urllib.parse.urlencode(params)
    return redirect(auth_url)

@app.route('/auth/callback', methods=['GET'])
def auth_callback():
    code = request.args.get('code')
    if not code:
        return "Authorization code missing", 400
        
    google_client_id = os.environ.get("GOOGLE_CLIENT_ID", "").strip()
    google_client_secret = os.environ.get("GOOGLE_CLIENT_SECRET", "").strip()
    
    redirect_uri = request.host_url.rstrip('/') + '/auth/callback'
    # Force HTTPS when running on production (Render.com)
    if 'localhost' not in request.host and '127.0.0.1' not in request.host and '192.168.' not in request.host:
        if redirect_uri.startswith("http://"):
            redirect_uri = redirect_uri.replace("http://", "https://", 1)
            
    token_url = "https://oauth2.googleapis.com/token"
    payload = {
        "code": code,
        "client_id": google_client_id,
        "client_secret": google_client_secret,
        "redirect_uri": redirect_uri,
        "grant_type": "authorization_code"
    }
    
    try:
        resp = requests.post(token_url, data=payload, timeout=5)
        if resp.status_code != 200:
            return f"Failed to retrieve tokens from Google: {resp.text}", 400
            
        tokens = resp.json()
        access_token = tokens.get('access_token')
        
        userinfo_url = "https://www.googleapis.com/oauth2/v3/userinfo"
        headers = {"Authorization": f"Bearer {access_token}"}
        profile_resp = requests.get(userinfo_url, headers=headers, timeout=5)
        
        if profile_resp.status_code != 200:
            return "Failed to fetch user profile from Google", 400
            
        profile = profile_resp.json()
        session['user'] = {
            "name": profile.get("name", "Google User"),
            "email": profile.get("email", ""),
            "picture": profile.get("picture", "")
        }
        return redirect('/')
    except Exception as e:
        return f"Authentication error: {str(e)}", 500

@app.route('/auth/login_email', methods=['POST'])
def auth_login_email():
    data = request.get_json() or {}
    email = data.get('email', '').strip().lower()
    password = data.get('password', '')
    
    if not email or not password:
        return jsonify({"error": "Email and password are required"}), 400
        
    db = load_users_data()
    user = db["users"].get(email)
    
    if not user:
        # Auto-registration if account does not exist
        name = email.split('@')[0].replace('.', ' ').replace('-', ' ').title()
        db["users"][email] = {
            "name": name,
            "password": hash_password(password),
            "picture": ""
        }
        save_users_data(db)
        
        user_info = {
            "name": name,
            "email": email,
            "picture": ""
        }
        session['user'] = user_info
        return jsonify({"status": "success", "user": user_info, "message": "Registered and logged in"})
    else:
        # Check password for existing account
        if user.get("password") != hash_password(password):
            return jsonify({"error": "Invalid password for this account"}), 401
            
        user_info = {
            "name": user.get("name"),
            "email": email,
            "picture": user.get("picture", "")
        }
        session['user'] = user_info
        return jsonify({"status": "success", "user": user_info})

@app.route('/auth/login_guest', methods=['POST'])
def login_guest():
    guest_user = {
        "name": "Guest User",
        "email": "guest@sirivela.local",
        "picture": ""
    }
    session['user'] = guest_user
    return jsonify({"status": "success", "user": guest_user})

@app.route('/auth/logout', methods=['POST'])
def logout():
    session.pop('user', None)
    return jsonify({"status": "success"})

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
            try:
                rag_helper.init_rag(client)
            except Exception as e:
                print(f"RAG Error: Failed to initialize RAG on dynamic client load: {e}")
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

        # Auto-index any new or modified RAG documents
        try:
            rag_helper.init_rag(client)
        except Exception as ex:
            print(f"RAG Warning: Auto-indexing documents failed: {ex}")

        # Retrieve relevant grounding context
        rag_context = ""
        sources = []
        try:
            results = rag_helper.get_relevant_context(client, user_message, num_results=3)
            if results:
                rag_context = "\n\nGrounding Context from local documents:\n"
                for r in results:
                    rag_context += f"- From [{r['filename']}]: {r['text']}\n"
                    if r['filename'] not in sources:
                        sources.append(r['filename'])
        except Exception as ex:
            print(f"RAG Warning: Context retrieval failed: {ex}")

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
            "You are SIRIVELA AI, a friendly, helpful, and concise conversational voice assistant and chatbot. "
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

        if rag_context:
            system_instruction += (
                f"{rag_context}\n"
                f"CRITICAL: The grounding context above contains information directly from the user's uploaded local documents. "
                f"Use this grounding context to answer the user's question. If the answer is found in the context, base your answer on it. "
                f"If the context does not contain the answer, you may answer using your general knowledge or search tool."
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
            'timestamp': datetime.now().strftime('%H:%M:%S'),
            'sources': sources
        })

        save_history_data(history_data)

        return jsonify({
            'response': assistant_text,
            'session_id': session_id,
            'session_title': session["title"],
            'sources': sources
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
