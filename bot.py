import os
import requests
from slack_bolt import App
from slack_bolt.adapter.flask import SlackRequestHandler
from flask import Flask, request
from dotenv import load_dotenv
import time
load_dotenv()

SLACK_BOT_TOKEN = ""
SLACK_SIGNING_SECRET = ""
MCP_URL = "http://localhost:8000/agent"

# Initialize Flask app FIRST
flask_app = Flask(__name__)

# Store processed events to prevent duplicates
processed_events = set()
MAX_CACHE_SIZE = 1000

# Initialize Slack app with proper error handling
try:
    app = App(
        token=SLACK_BOT_TOKEN,
        signing_secret=SLACK_SIGNING_SECRET,
        process_before_response=True
    )
except Exception as e:
    print(f"Failed to initialize Slack app: {e}")
    print("Make sure SLACK_SIGNING_SECRET is set correctly!")
    exit(1)

# Initialize request handler
handler = SlackRequestHandler(app)

def is_duplicate_event(event_id, event_time):
    """Check if we've already processed this event"""
    event_key = f"{event_id}_{event_time}"
    
    if event_key in processed_events:
        return True
    
    # Add to processed events
    processed_events.add(event_key)
    
    # Clean up old events if cache gets too large
    if len(processed_events) > MAX_CACHE_SIZE:
        # Remove oldest half of events (simple cleanup)
        old_events = list(processed_events)[:MAX_CACHE_SIZE // 2]
        for old_event in old_events:
            processed_events.discard(old_event)
    
    return False

# Listen for app mentions ONLY
@app.event("app_mention")
def handle_app_mention_events(body, say, logger, client):
    try:
        logger.info(f"Received app mention: {body}")
        
        # Get event details for deduplication
        event = body.get("event", {})
        event_id = event.get("client_msg_id") or event.get("ts")
        event_time = event.get("ts")
        
        # Check for duplicate events
        if is_duplicate_event(event_id, event_time):
            logger.info(f"Skipping duplicate event: {event_id}")
            print(f"⏭️ Skipping duplicate event: {event_id}")
            return
        
        user_id = event["user"]
        text = event["text"]
        
        # Get user info to get the actual username
        try:
            user_info = client.users_info(user=user_id)
            username = user_info["user"]["name"]
            display_name = user_info["user"].get("display_name") or username
        except Exception as e:
            logger.warning(f"Could not get user info: {e}")
            username = user_id  # Fallback to user ID
            display_name = user_id
        
        # Log the mention to console
        print(f"🤖 Bot mentioned by user {username} ({display_name}): {text}")
        
        # Send message with user info embedded in the message
        enhanced_message = f"Username is: {username}, Display name is: {display_name}, User message is: {text}"
        payload = {
            "message": enhanced_message
        }
        
        logger.info(f"Sending to MCP: {payload}")
        print(f"📤 Sending to MCP server: {payload}")
        
        res = requests.post(MCP_URL, json=payload, timeout=30)
        res.raise_for_status()
        
        mcp_response = res.json()
        logger.info(f"Full MCP response: {mcp_response}")
        print(f"📥 Full MCP response: {mcp_response}")
        
        # Check if the response indicates success
        if mcp_response.get("success", False):
            # Extract just the response message
            reply = mcp_response.get("response", "✅ Request processed successfully")
            say(reply)
        else:
            # Handle error case
            error_msg = mcp_response.get("error", "Unknown error occurred")
            print(f"❌ MCP server returned error: {error_msg}")
            logger.error(f"MCP server error: {error_msg}")
            say("Sorry, I couldn't process the request at the moment.")
        
    except requests.exceptions.RequestException as e:
        logger.error(f"MCP request failed: {e}")
        print(f"❌ MCP request failed: {e}")
        say("Sorry, I couldn't process the request at the moment.")
    except Exception as e:
        logger.error(f"General error: {e}")
        print(f"❌ General error: {e}")
        say("Sorry, I couldn't process the request at the moment.")

# REMOVED the generic message handler to prevent duplicate processing
# The app_mention handler above will handle all app mentions

# Flask routes
@flask_app.route("/slack/events", methods=["POST"])
def slack_events():
    try:
        data = request.get_json()
        
        # Step 1: Handle Slack URL verification
        if data and data.get("type") == "url_verification":
            challenge = data.get("challenge")
            print(f"🔐 URL verification challenge received: {challenge}")
            return challenge, 200, {'Content-Type': 'text/plain'}
        
        # Log the incoming request for debugging (but less verbose)
        print(f"📨 Received POST to /slack/events - Event type: {data.get('event', {}).get('type', 'unknown')}")
        
        # Step 2: Let SlackRequestHandler handle all other events
        response = handler.handle(request)
        return response
        
    except Exception as e:
        print(f"❌ Error in slack_events: {e}")
        return {"error": str(e)}, 500

@flask_app.route("/health", methods=["GET"])
def health_check():
    return {"status": "healthy", "slack_configured": SLACK_SIGNING_SECRET != "your_signing_secret_here"}, 200

@flask_app.route("/test", methods=["POST"])  
def test_post():
    return {"message": "POST is working", "data": request.get_json()}, 200

if __name__ == "__main__":
    print("🚀 Starting Slack bot...")
    print(f"Bot token: {SLACK_BOT_TOKEN[:20]}...")
    print(f"Signing secret configured: {SLACK_SIGNING_SECRET != 'your_signing_secret_here'}")
    
    # Run Flask app with debug=False in production
    flask_app.run(
        host="0.0.0.0",
        port=3002,
        debug=False  # Changed to False to prevent development server issues
        )
[ec2-user@ip-172-31-3-79 slack-rooms]$ cat agnoagent.py 
import asyncio
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
from typing import Optional
import io
from contextlib import redirect_stdout
from datetime import datetime
from agno.agent import Agent
from agno.models.google import Gemini
from agno.tools.mcp import MCPTools
from agno.tools.toolkit import Toolkit

# FastAPI app instance
app = FastAPI(title="Agent API", description="API for MCP Agent interactions")

# Request model
class PromptRequest(BaseModel):
    message: str

# Response model
class AgentResponse(BaseModel):
    response: str
    success: bool
    error: Optional[str] = None

# Configuration
SERVER_URL = "http://localhost:3001/mcp"
GEMINI_API_KEY = "AIzaSyB_nXNqTCAiZZH6SqYRUsPwxtGa6kDlay8"

# Custom datetime tool
def get_current_datetime() -> str:
    """Get the current date and time in a readable format"""
    now = datetime.now()
    return now.strftime("%Y-%m-%d %H:%M:%S (%A)")

# Create toolkit with datetime tool
datetime_toolkit = Toolkit()
datetime_toolkit.register(get_current_datetime)

async def run_agent(message: str) -> str:
    """Run agent with MCP connection"""
    try:
        print(f"\n=== AGENT REQUEST ===")
        print(f"Message: {message}")
        
        # Capture output
        output_buffer = io.StringIO()
        
        async with MCPTools(transport="streamable-http", url=SERVER_URL) as mcp_tools:
            agent = Agent(
                model=Gemini(
                    id="gemini-1.5-pro-002",  # High context, very good model
                    api_key=GEMINI_API_KEY
                ),
                tools=[mcp_tools, datetime_toolkit],
                show_tool_calls=False,
                markdown=False,
                instructions=[
                    "You are a meeting room booking agent.",
                    "IMPORTANT: Always check the current date and time first using get_current_datetime() before processing any booking request.",
                    "You can only book meetings for future dates and times. Do not allow bookings for past dates or times.",
                    "If no date is specified, use today's date but ensure the time is in the future.",
                    "If a user tries to book a meeting in the past, politely decline and suggest future time slots.",
                    "When processing booking requests, validate that the requested date/time is after the current date/time and always ask for name if not provided"
                ]
            )
            
            # Get the agent response
            print("🔄 Calling agent.arun()...")
            try:
                response_obj = await agent.arun(message=message)
                print(f"✅ Got response object: {type(response_obj)}")
                
                # Extract the actual content from the response object
                if hasattr(response_obj, 'content'):
                    raw_response = str(response_obj.content)
                elif hasattr(response_obj, 'text'):
                    raw_response = str(response_obj.text)
                else:
                    raw_response = str(response_obj)
                    
            except Exception as e:
                print(f"❌ arun() failed: {e}")
                print("🔄 Trying aprint_response() fallback...")
                
                # Fallback to aprint_response with output capture
                with redirect_stdout(output_buffer):
                    await agent.aprint_response(message=message, stream=False, markdown=False)
                
                raw_response = output_buffer.getvalue()
            
            print(f"\n=== RESPONSE ===")
            print(raw_response)
            print(f"=== END RESPONSE ===\n")
            
            return raw_response
            
    except Exception as e:
        print(f"ERROR in run_agent: {str(e)}")
        raise HTTPException(status_code=500, detail=f"Agent error: {str(e)}")

@app.post("/agent", response_model=AgentResponse)
async def process_agent_request(request: PromptRequest):
    """
    Process a message through the agent and return the response.
    
    - **message**: The prompt/message to send to the agent
    """
    try:
        print(f"\n🚀 Processing request: {request.message}")
        
        response = await run_agent(request.message)
        cleaned_response = response.strip()
        
        print(f"\n=== FINAL RESPONSE ===")
        print(cleaned_response)
        print(f"=== END FINAL RESPONSE ===\n")
        
        return AgentResponse(
            response=cleaned_response,
            success=True
        )
        
    except HTTPException:
        raise
    except Exception as e:
        print(f"❌ ERROR in process_agent_request: {str(e)}")
        return AgentResponse(
            response="",
            success=False,
            error=str(e)
        )

@app.get("/")
async def root():
    """Health check endpoint"""
    return {"message": "Agent API is running", "status": "healthy"}

@app.get("/health")
async def health_check():
    """Detailed health check"""
    return {
        "status": "healthy",
        "server_url": SERVER_URL,
        "model": "gemini-1.5-pro-002",
        "endpoints": {
            "agent": "/agent (POST)",
            "health": "/health (GET)"
        }
    }

@app.post("/agent/simple")
async def process_simple_request(request: PromptRequest):
    """
    Simplified endpoint that returns just the agent response as plain text.
    """
    try:
        print(f"\n📝 Simple request: {request.message}")
        
        response = await run_agent(request.message)
        cleaned_response = response.strip()
        
        return {"response": cleaned_response}
        
    except Exception as e:
        print(f"❌ ERROR in process_simple_request: {str(e)}")
        raise HTTPException(status_code=500, detail=str(e))

if __name__ == "__main__":
    import uvicorn
    
    print("🎯 Starting FastAPI Agent Server with Google Gemini...")
    print(f"Server URL: {SERVER_URL}")
    print(f"Model: gemini-1.5-pro-002")
    
    # Run the FastAPI server
    uvicorn.run(
        "agnoagent:app",  # Change "main" to your filename if different
        host="0.0.0.0",
        port=8000,
        reload=True
    )