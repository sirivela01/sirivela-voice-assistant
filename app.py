import os
import json
import requests
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

        # Store the conversation entry
        history_file = os.path.join(basedir, 'history.json')
        history_data = []
        if os.path.exists(history_file):
            try:
                with open(history_file, 'r', encoding='utf-8') as f:
                    history_data = json.load(f)
            except Exception:
                history_data = []

        history_data.append({
            'sender': 'user',
            'text': user_message,
            'timestamp': datetime.now().strftime('%H:%M:%S')
        })
        history_data.append({
            'sender': 'assistant',
            'text': assistant_text,
            'timestamp': datetime.now().strftime('%H:%M:%S')
        })

        try:
            with open(history_file, 'w', encoding='utf-8') as f:
                json.dump(history_data, f, indent=4)
        except Exception as e:
            print(f"Error saving history: {e}")

        return jsonify({'response': assistant_text})

    except Exception as e:
        print(f"Error calling Gemini API: {e}")
        return jsonify({'error': f"Gemini API error: {str(e)}"}), 500

@app.route('/history', methods=['GET'])
def get_history():
    history_file = os.path.join(basedir, 'history.json')
    if os.path.exists(history_file):
        try:
            with open(history_file, 'r', encoding='utf-8') as f:
                data = json.load(f)
                return jsonify(data)
        except Exception as e:
            return jsonify({'error': str(e)}), 500
    return jsonify([])

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

@app.route('/image', methods=['GET'])
def get_image():
    query = request.args.get('q')
    if not query:
        return jsonify({'error': 'No query provided'}), 400

    headers = {'User-Agent': 'SirivelaVoiceAssistant/1.0 (contact@example.com)'}
    fallback_url = f"https://loremflickr.com/600/400/{query}"

    try:
        # Step 1: Search Wikipedia for the best matching page title
        search_url = f"https://en.wikipedia.org/w/api.php?action=query&list=search&srsearch={query}&format=json"
        r = requests.get(search_url, headers=headers, timeout=5)
        search_data = r.json()
        search_results = search_data.get('query', {}).get('search', [])
        
        if search_results:
            best_title = search_results[0]['title']
            
            # Step 2: Fetch the page image for the best title
            img_url = f"https://en.wikipedia.org/w/api.php?action=query&titles={best_title}&prop=pageimages&format=json&pithumbsize=600"
            r2 = requests.get(img_url, headers=headers, timeout=5)
            img_data = r2.json()
            pages = img_data.get('query', {}).get('pages', {})
            
            for page_id, page_info in pages.items():
                thumbnail = page_info.get('thumbnail', {}).get('source')
                if thumbnail:
                    return jsonify({'url': thumbnail})
    except Exception as e:
        print(f"Error fetching wiki image for {query}: {e}")
        
    return jsonify({'url': fallback_url})

if __name__ == '__main__':
    # Run the server
    app.run(debug=True, port=5000)
