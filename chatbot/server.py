#!/usr/bin/python3
"""
Web based ChatBot Example

Web chat client for OpenAI and the llama-cpp-python[server] OpenAI API Compatible 
Python Flask based Web Server. Provides a simple web based chat session.

Features:
  * Uses OpenAI API
  * Works with local hosted OpenAI compatible llama-cpp-python[server]
  * Retains conversational context for LLM
  * Uses response stream to render LLM chunks instead of waiting for full response

Requirements:
  * pip install openai flask flask-socketio bs4

Environmental variables:
  * OPENAI_API_KEY - Required only for OpenAI
  * OPENAI_API_BASE - URL to OpenAI API Server or locally hosted version
  * AGENT_NAME - Name for Bot
  * AGENT_NAME - LLM Model to Use

Running a llama-cpp-python server:
  * CMAKE_ARGS="-DLLAMA_CUBLAS=on" FORCE_CMAKE=1 pip install llama-cpp-python
  * pip install llama-cpp-python[server]
  * python3 -m llama_cpp.server --model models/7B/ggml-model.bin

Author: Jason A. Cox
23 Sept 2023
https://github.com/jasonacox/TinyLLM

"""
import os
import time
import datetime
import threading
import requests
from bs4 import BeautifulSoup
from flask import Flask, render_template, request, jsonify
from flask_socketio import SocketIO
import openai

VERSION = "v0.3"

MAXTOKENS = 2048
TEMPERATURE = 0.7

# Configuration Settings - Showing local LLM
openai.api_key = os.environ.get("OPENAI_API_KEY", "DEFAULT_API_KEY")            # Required, use bogus string for Llama.cpp
openai.api_base = os.environ.get("OPENAI_API_BASE", "http://localhost:8000/v1") # Use API endpoint or comment out for OpenAI
agentname = os.environ.get("AGENT_NAME", "Jarvis")                              # Set the name of your bot
mymodel = os.environ.get("MY_MODEL", "models/7B/gguf-model.bin")                # Pick model to use e.g. gpt-3.5-turbo for OpenAI
DEBUG = os.environ.get("DEBUG", "False") == "True"

# Configure Flask App and SocketIO
app = Flask(__name__)
socketio = SocketIO(app)

# Globals
prompt = ""
visible = True
remember = True

# Set base prompt and initialize the context array for conversation dialogue
current_date = datetime.datetime.now()
formatted_date = current_date.strftime("%m/%d/%Y")
baseprompt = "You are %s, a highly intelligent assistant. Keep your answers brief and accurate. Current date is %s." % (agentname, formatted_date)
context = [{"role": "system", "content": baseprompt}]

# Function - Send user prompt to LLM for response
def ask(prompt):
    global context, remember

    response = False
    print(f"Context size = {len(context)}")
    while not response:
        try:
            # remember context
            context.append({"role": "user", "content": prompt})
            response = openai.ChatCompletion.create(
                model=mymodel,
                max_tokens=MAXTOKENS,
                stream=True, # Send response chunks as LLM computes next tokens
                temperature=TEMPERATURE,
                messages=context,
            )
        except openai.error.OpenAIError as e:
            print(f"ERROR {e}")
            context.pop()
            if "maximum context length" in str(e):
                if len(prompt) > 1000:
                    # assume we have very large prompt - cut out the middle
                    prompt = prompt[:len(prompt)//4] + " ... " + prompt[-len(prompt)//4:]
                    print(f"Reduce prompt size - now {len(prompt)}")
                elif len(context) > 4:
                    # our context has grown too large, truncate the top
                    context = context[:1] + context[3:]
                    print(f"Truncate context: {len(context)}")
                else:
                    # our context has grown too large, reset
                    context = [{"role": "system", "content": baseprompt}]   
                    print(f"Reset context {len(context)}")
                    socketio.emit('update', {'update': '[Memory Reset]', 'voice': 'user'})

    if not remember:
        remember =True
        context.pop()
    return response

def extract_text_from_blog(url):
    try:
        response = requests.get(url)
        if response.status_code == 200:
            # Parse the HTML content of the page
            soup = BeautifulSoup(response.text, 'html.parser')

            # Find and extract all text within paragraph (p) tags
            paragraphs = soup.find_all('p')

            # Concatenate the text from all paragraphs
            blog_text = '\n'.join([p.get_text() for p in paragraphs])

            return blog_text
        else:
            print(f"Failed to fetch the webpage. Status code: {response.status_code}")
    except Exception as e:
        print(f"An error occurred: {str(e)}")

@app.route('/')
def index():
    global context, baseprompt
    # Reset context
    context = [{"role": "system", "content": baseprompt}]
    return render_template('index.html')

@app.route('/version')
def version():
    global VERSION, DEBUG
    if DEBUG:
        return jsonify({'version': "%s DEBUG MODE" % VERSION})
    return jsonify({'version': VERSION})

@app.route('/send_message', methods=['POST'])
def send_message():
    global prompt, visible, remember
    # Handle incoming user prompts and store them
    data = request.json
    print("Received Data:", data)
    p = data["prompt"]
    visible = data["show"]
    # Did we get asked to fetch a URL?
    if p.startswith("http"):
        # fetch blog
        url = p
        visible = False
        remember = False # Don't add blog to context window, just summary
        blogtext = extract_text_from_blog(p.strip())
        print(f"* Reading {len(blogtext)} bytes {url}")
        socketio.emit('update', {'update': '[Reading: %s]' % url, 'voice': 'user'})
        prompt = "Summarize the following text:\n" + blogtext
    else:
        prompt = p
    return jsonify({'status': 'Message received'})

# Convert each character to its hex representation
def string_to_hex(input_string):
    hex_values = [hex(ord(char)) for char in input_string]
    return hex_values

# Continuous thread to send updates to connected clients
def send_update(): 
    global x, prompt, context

    while True:
        if prompt == "":
            time.sleep(.5)
        else:
            update_text = prompt 
            if visible:
                socketio.emit('update', {'update': update_text, 'voice': 'user'})
            try:
                # Ask LLM for answers
                response=ask(prompt)
                completion_text = ''
                # iterate through the stream of events and print it
                for event in response:
                    event_text = event['choices'][0]['delta']
                    if 'content' in event_text:
                        chunk = event_text.content
                        completion_text += chunk
                        if DEBUG:
                            print(string_to_hex(chunk), end="")
                            print(f" = [{chunk}]")
                        socketio.emit('update', {'update': chunk, 'voice': 'ai'})
                # remember context
                context.append({"role": "assistant", "content" : completion_text})
            except:
                # Unable to process prompt, give error
                socketio.emit('update', {'update': 'An error occurred - unable to complete.', 'voice': 'ai'})
                # Reset context
                context = [{"role": "system", "content": baseprompt}]
            # Signal response is done
            socketio.emit('update', {'update': '', 'voice': 'done'})
            prompt = ''
            if DEBUG:
                print(f"AI: {completion_text}")

# Create a background thread to send updates
update_thread = threading.Thread(target=send_update)
update_thread.daemon = True  # Thread will terminate when the main program exits
update_thread.start()

# Start server
if __name__ == '__main__':
    socketio.run(app, host='0.0.0.0', port=5000, debug=DEBUG, allow_unsafe_werkzeug=True)

