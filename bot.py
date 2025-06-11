import os
import asyncio
import aiohttp
import uuid
from fastapi import FastAPI, Request, HTTPException
from fastapi.responses import PlainTextResponse
from slack_bolt.async_app import AsyncApp
from slack_bolt.adapter.fastapi.async_handler import AsyncSlackRequestHandler
from dotenv import load_dotenv
import time
from typing import Dict, Any
import uvicorn
import json

load_dotenv()

SLACK_BOT_TOKEN = ""
SLACK_SIGNING_SECRET = ""
MCP_URL = "http://localhost:8000/agent"

# Initialize FastAPI app
app = FastAPI(
    title="Slack Bot API",
    description="Production-ready Slack bot with FastAPI and enhanced tracking",
    version="1.0.0"
)

# Store processed events to prevent duplicates
processed_events = set()
MAX_CACHE_SIZE = 1000

# Initialize Slack app with async support
try:
    slack_app = AsyncApp(
        token=SLACK_BOT_TOKEN,
        signing_secret=SLACK_SIGNING_SECRET,
        process_before_response=True
    )
    print("✅ Slack app initialized successfully")
except Exception as e:
    print(f"❌ Failed to initialize Slack app: {e}")
    print("Make sure SLACK_SIGNING_SECRET is set correctly!")
    exit(1)

# Initialize async request handler
handler = AsyncSlackRequestHandler(slack_app)

def is_duplicate_event(event_id: str, event_time: str) -> bool:
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
@slack_app.event("app_mention")
async def handle_app_mention_events(body: Dict[Any, Any], say, logger, client):
    try:
        # Generate unique request ID for tracking
        request_id = str(uuid.uuid4())[:8]
        
        logger.info(f"[{request_id}] Received app mention: {body}")
        
        # Get event details for deduplication
        event = body.get("event", {})
        event_id = event.get("client_msg_id") or event.get("ts")
        event_time = event.get("ts")
        
        print(f"[{request_id}] 🎯 Processing event: {event_id} at {event_time}")
        
        # Check for duplicate events
        if is_duplicate_event(event_id, event_time):
            logger.info(f"[{request_id}] Skipping duplicate event: {event_id}")
            print(f"[{request_id}] ⏭️ Skipping duplicate event: {event_id}")
            return
        
        user_id = event["user"]
        text = event["text"]
        
        # Get user info to get the actual username
        try:
            user_info = await client.users_info(user=user_id)
            username = user_info["user"]["name"]
            display_name = user_info["user"].get("display_name") or username
        except Exception as e:
            logger.warning(f"[{request_id}] Could not get user info: {e}")
            username = user_id  # Fallback to user ID
            display_name = user_id
        
        # Log the mention to console
        print(f"[{request_id}] 🤖 Bot mentioned by user {username} ({display_name}): {text}")
        
        # Send message with user info embedded in the message
        enhanced_message = f"Username is: {username}, Display name is: {display_name}, User message is: {text}"
        payload = {
            "message": enhanced_message
        }
        
        logger.info(f"[{request_id}] Sending to MCP: {payload}")
        print(f"[{request_id}] 📤 MAKING HTTP REQUEST to MCP server: {payload}")
        
        # Use async HTTP client
        async with aiohttp.ClientSession() as session:
            try:
                async with session.post(MCP_URL, json=payload, timeout=30) as response:
                    print(f"[{request_id}] 📥 RECEIVED HTTP RESPONSE - Status: {response.status}")
                    response.raise_for_status()
                    mcp_response = await response.json()
                    
                    logger.info(f"[{request_id}] Full MCP response: {mcp_response}")
                    print(f"[{request_id}] 📥 Full MCP response: {mcp_response}")
                    
                    # Check if the response indicates success
                    if mcp_response.get("success", False):
                        # Extract just the response message
                        reply = mcp_response.get("response", "✅ Request processed successfully")
                        print(f"[{request_id}] 💬 SENDING TO SLACK: {reply}")
                        await say(reply)
                        print(f"[{request_id}] ✅ MESSAGE SENT TO SLACK")
                    else:
                        # Handle error case
                        error_msg = mcp_response.get("error", "Unknown error occurred")
                        print(f"[{request_id}] ❌ MCP server returned error: {error_msg}")
                        logger.error(f"[{request_id}] MCP server error: {error_msg}")
                        await say("Sorry, I couldn't process the request at the moment.")
                        
            except aiohttp.ClientError as e:
                logger.error(f"[{request_id}] MCP request failed: {e}")
                print(f"[{request_id}] ❌ MCP request failed: {e}")
                await say("Sorry, I couldn't process the request at the moment.")
        
        print(f"[{request_id}] 🏁 FINISHED processing event")
        
    except Exception as e:
        logger.error(f"General error: {e}")
        print(f"❌ General error: {e}")
        await say("Sorry, I couldn't process the request at the moment.")

# FastAPI routes
@app.post("/slack/events")
async def slack_events(request: Request):
    """Handle Slack events"""
    try:
        # Get request body
        body = await request.body()
        
        # Parse JSON for logging
        try:
            data = json.loads(body)
            
            # Handle Slack URL verification
            if data and data.get("type") == "url_verification":
                challenge = data.get("challenge")
                print(f"🔐 URL verification challenge received: {challenge}")
                return PlainTextResponse(challenge)
            
            # Log the incoming request for debugging (but less verbose)
            event_type = data.get('event', {}).get('type', 'unknown')
            print(f"📨 Received POST to /slack/events - Event type: {event_type}")
            
        except json.JSONDecodeError:
            print("📨 Received POST to /slack/events - Could not parse JSON")
        
        # Let AsyncSlackRequestHandler handle all other events
        return await handler.handle(request)
        
    except Exception as e:
        print(f"❌ Error in slack_events: {e}")
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/health")
async def health_check():
    """Health check endpoint"""
    return {
        "status": "healthy", 
        "slack_configured": SLACK_SIGNING_SECRET != "your_signing_secret_here",
        "server_type": "FastAPI",
        "async_support": True,
        "mcp_url": MCP_URL,
        "processed_events_count": len(processed_events)
    }

@app.post("/test")
async def test_post(request: Request):
    """Test POST endpoint"""
    body = await request.json()
    return {"message": "POST is working", "data": body}

@app.get("/")
async def root():
    """Root endpoint"""
    return {
        "message": "Slack Bot API is running with enhanced tracking",
        "framework": "FastAPI",
        "docs": "/docs",
        "health": "/health",
        "features": [
            "App mention handling",
            "Duplicate event prevention",
            "Request ID tracking",
            "Enhanced logging",
            "MCP server integration"
        ]
    }

@app.get("/stats")
async def get_stats():
    """Get bot statistics"""
    return {
        "processed_events": len(processed_events),
        "max_cache_size": MAX_CACHE_SIZE,
        "mcp_url": MCP_URL,
        "bot_status": "active"
    }

if __name__ == "__main__":
    print("🚀 Starting FastAPI Slack bot with enhanced tracking...")
    print(f"Bot token: {SLACK_BOT_TOKEN[:20]}..." if SLACK_BOT_TOKEN else "Bot token: Not set")
    print(f"Signing secret configured: {bool(SLACK_SIGNING_SECRET)}")
    print(f"MCP URL: {MCP_URL}")
    print("🌟 Using FastAPI with Uvicorn (production-ready)")
    print("📊 Enhanced features: Request ID tracking, duplicate prevention, detailed logging")
    
    # Run with Uvicorn (production ASGI server)
    uvicorn.run(
        "bot:app",  # Change this to match your filename
        host="0.0.0.0",
        port=3002,
        reload=False,  # Set to True for development
        workers=1,     # Increase for production
        access_log=True
    )