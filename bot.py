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
import hashlib

load_dotenv()

# IMPORTANT: Fix the token assignment
# Bot tokens start with "xoxb-", signing secrets are shorter hex strings
SLACK_BOT_TOKEN = ""
SLACK_SIGNING_SECRET =  " "
MCP_URL = "http://localhost:8000/agent"

# Initialize FastAPI app
app = FastAPI(
    title="Slack Bot API",
    description="Production-ready Slack bot with response deduplication",
    version="1.0.0"
)

# Store processed events to prevent duplicates
processed_events = set()
MAX_CACHE_SIZE = 1000

# Store sent responses to prevent duplicate responses
sent_responses = {}
RESPONSE_CACHE_TTL = 600  # 10 minutes

# Track processing tasks
processing_tasks = {}

# Initialize Slack app with async support
try:
    slack_app = AsyncApp(
        token=SLACK_BOT_TOKEN,
        signing_secret=SLACK_SIGNING_SECRET,
        process_before_response=True
    )
    print("✅ Slack app initialized successfully")
    print(f"Bot token starts with: {SLACK_BOT_TOKEN[:10]}...")
    print(f"Signing secret length: {len(SLACK_SIGNING_SECRET)}")
except Exception as e:
    print(f"❌ Failed to initialize Slack app: {e}")
    print("Token format check:")
    print(f"  Bot token starts with 'xoxb-': {SLACK_BOT_TOKEN.startswith('xoxb-')}")
    print(f"  Signing secret length: {len(SLACK_SIGNING_SECRET)} (should be 32)")
    exit(1)

# Initialize async request handler
handler = AsyncSlackRequestHandler(slack_app)

def create_response_key(event_data: dict, response_text: str) -> str:
    """Create a unique key for response deduplication"""
    event_ts = event_data.get("ts", "")
    channel = event_data.get("channel", "")
    user = event_data.get("user", "")
    # Include hash of response to make key unique per response
    response_hash = hashlib.md5(response_text.encode()).hexdigest()[:8]
    return f"{event_ts}_{channel}_{user}_{response_hash}"

def clean_old_responses():
    """Clean up old response cache entries"""
    current_time = time.time()
    expired_keys = [
        key for key, timestamp in sent_responses.items() 
        if current_time - timestamp > RESPONSE_CACHE_TTL
    ]
    for key in expired_keys:
        del sent_responses[key]
    if expired_keys:
        print(f"🧹 Cleaned {len(expired_keys)} old response cache entries")

async def safe_say(say_func, message: str, event_data: dict, request_id: str) -> bool:
    """Safely send message to Slack with deduplication"""
    try:
        response_key = create_response_key(event_data, message)
        current_time = time.time()
        
        # Check if we've already sent this exact response recently
        if response_key in sent_responses:
            time_diff = current_time - sent_responses[response_key]
            if time_diff < 30:  # Don't send same response within 30 seconds
                print(f"[{request_id}] 🚫 BLOCKING duplicate response (sent {time_diff:.1f}s ago)")
                return False
        
        # Send the message
        print(f"[{request_id}] 💬 SENDING TO SLACK: {message[:100]}...")
        await say_func(message)
        
        # Record that we sent this response
        sent_responses[response_key] = current_time
        print(f"[{request_id}] ✅ MESSAGE SENT - Cached response key: {response_key}")
        
        # Clean up old entries periodically
        if len(sent_responses) > 50:
            clean_old_responses()
        
        return True
        
    except Exception as e:
        print(f"[{request_id}] ❌ Error sending message to Slack: {e}")
        return False

async def process_mention_async(event_data: dict, say_func, client, logger, request_id: str):
    """Process mention asynchronously with immediate acknowledgment and response update"""
    initial_message_ts = None
    channel = event_data.get("channel")
    
    try:
        user_id = event_data["user"]
        text = event_data["text"]
        
        # Get user info
        try:
            user_info = await client.users_info(user=user_id)
            username = user_info["user"]["name"]
            display_name = user_info["user"].get("display_name") or username
        except Exception as e:
            logger.warning(f"[{request_id}] Could not get user info: {e}")
            username = user_id
            display_name = user_id
        
        print(f"[{request_id}] 🤖 Bot mentioned by {username}: {text[:50]}...")
        
        # STEP 1: Send immediate acknowledgment message
        initial_message = "🤔 Processing your request... please hold on a moment."
        try:
            # Use client.chat_postMessage to get the message timestamp for updates
            initial_response = await client.chat_postMessage(
                channel=channel,
                text=initial_message
            )
            initial_message_ts = initial_response["ts"]
            print(f"[{request_id}] ✅ Sent initial message with ts: {initial_message_ts}")
        except Exception as e:
            print(f"[{request_id}] ❌ Failed to send initial message: {e}")
            # Fallback to regular say function
            await safe_say(say_func, initial_message, event_data, request_id)
        
        # STEP 2: Prepare payload for MCP server
        enhanced_message = f"Username is: {username}, Display name is: {display_name}, User message is: {text}"
        payload = {"message": enhanced_message}
        
        print(f"[{request_id}] 📤 Sending to MCP server...")
        
        # STEP 3: Make HTTP request to MCP server
        async with aiohttp.ClientSession() as session:
            try:
                async with session.post(MCP_URL, json=payload, timeout=30) as response:
                    print(f"[{request_id}] 📥 MCP Response Status: {response.status}")
                    
                    if response.status == 200:
                        mcp_response = await response.json()
                        print(f"[{request_id}] MCP Response: {mcp_response}")
                        
                        if mcp_response.get("success", False):
                            reply = mcp_response.get("response", "✅ Request processed successfully")
                            
                            # STEP 4: Update the initial message with the actual response
                            if initial_message_ts:
                                try:
                                    await client.chat_update(
                                        channel=channel,
                                        ts=initial_message_ts,
                                        text=reply
                                    )
                                    print(f"[{request_id}] ✅ Updated message with actual response")
                                except Exception as e:
                                    print(f"[{request_id}] ❌ Failed to update message: {e}")
                                    # Fallback: send new message
                                    await safe_say(say_func, reply, event_data, request_id)
                            else:
                                await safe_say(say_func, reply, event_data, request_id)
                        else:
                            error_msg = mcp_response.get("error", "Unknown error occurred")
                            print(f"[{request_id}] ❌ MCP server error: {error_msg}")
                            error_response = "Sorry, I couldn't process the request at the moment."
                            
                            if initial_message_ts:
                                try:
                                    await client.chat_update(
                                        channel=channel,
                                        ts=initial_message_ts,
                                        text=error_response
                                    )
                                except:
                                    await safe_say(say_func, error_response, event_data, request_id)
                            else:
                                await safe_say(say_func, error_response, event_data, request_id)
                    else:
                        print(f"[{request_id}] ❌ MCP server returned {response.status}")
                        error_response = "Sorry, I couldn't process the request at the moment."
                        
                        if initial_message_ts:
                            try:
                                await client.chat_update(
                                    channel=channel,
                                    ts=initial_message_ts,
                                    text=error_response
                                )
                            except:
                                await safe_say(say_func, error_response, event_data, request_id)
                        else:
                            await safe_say(say_func, error_response, event_data, request_id)
                        
            except asyncio.TimeoutError:
                print(f"[{request_id}] ❌ MCP request timeout")
                timeout_response = "Sorry, the request timed out. Please try again."
                
                if initial_message_ts:
                    try:
                        await client.chat_update(
                            channel=channel,
                            ts=initial_message_ts,
                            text=timeout_response
                        )
                    except:
                        await safe_say(say_func, timeout_response, event_data, request_id)
                else:
                    await safe_say(say_func, timeout_response, event_data, request_id)
                    
            except aiohttp.ClientError as e:
                print(f"[{request_id}] ❌ MCP request failed: {e}")
                error_response = "Sorry, I couldn't connect to the processing server."
                
                if initial_message_ts:
                    try:
                        await client.chat_update(
                            channel=channel,
                            ts=initial_message_ts,
                            text=error_response
                        )
                    except:
                        await safe_say(say_func, error_response, event_data, request_id)
                else:
                    await safe_say(say_func, error_response, event_data, request_id)
        
        print(f"[{request_id}] 🏁 Finished processing")
        
    except Exception as e:
        logger.error(f"[{request_id}] Processing error: {e}")
        print(f"[{request_id}] ❌ Processing error: {e}")
        
        # Handle errors by updating the initial message if possible
        error_response = "Sorry, an unexpected error occurred."
        if initial_message_ts and channel:
            try:
                await client.chat_update(
                    channel=channel,
                    ts=initial_message_ts,
                    text=error_response
                )
            except:
                try:
                    await safe_say(say_func, error_response, event_data, request_id)
                except:
                    pass
        else:
            try:
                await safe_say(say_func, error_response, event_data, request_id)
            except:
                pass
    finally:
        # Clean up tracking
        event_key = f"{event_data.get('ts')}_{event_data.get('channel')}"
        processing_tasks.pop(event_key, None)

def create_event_key(event: dict) -> str:
    """Create a unique key for event deduplication"""
    event_ts = event.get("ts", "")
    event_channel = event.get("channel", "")
    event_user = event.get("user", "")
    event_text = event.get("text", "")
    
    # Create hash of the event content for uniqueness
    content_hash = hashlib.md5(f"{event_ts}_{event_channel}_{event_user}_{event_text}".encode()).hexdigest()[:8]
    return f"{event_ts}_{event_channel}_{content_hash}"

# Listen for app mentions
@slack_app.event("app_mention")
async def handle_app_mention_events(body: Dict[Any, Any], say, logger, client):
    request_id = str(uuid.uuid4())[:8]
    
    try:
        event = body.get("event", {})
        event_key = create_event_key(event)
        
        print(f"[{request_id}] 🎯 Received app mention - Event: {event_key}")
        
        # Check for duplicate events
        if event_key in processed_events:
            print(f"[{request_id}] ⏭️  DUPLICATE EVENT BLOCKED: {event_key}")
            return
        
        # Check if already processing
        if event_key in processing_tasks:
            print(f"[{request_id}] ⏭️  Already processing: {event_key}")
            return
        
        # Mark as processed and processing
        processed_events.add(event_key)
        processing_tasks[event_key] = request_id
        
        # Clean up old events
        if len(processed_events) > MAX_CACHE_SIZE:
            old_events = list(processed_events)[:MAX_CACHE_SIZE // 2]
            for old_event in old_events:
                processed_events.discard(old_event)
        
        print(f"[{request_id}] 🚀 Processing new event: {event_key}")
        
        # Process asynchronously
        asyncio.create_task(
            process_mention_async(event, say, client, logger, request_id)
        )
        
    except Exception as e:
        logger.error(f"[{request_id}] Handler error: {e}")
        print(f"[{request_id}] ❌ Handler error: {e}")

# FastAPI routes
@app.post("/slack/events")
async def slack_events(request: Request):
    """Handle Slack events"""
    try:
        body = await request.body()
        
        try:
            data = json.loads(body)
            
            # Handle URL verification
            if data and data.get("type") == "url_verification":
                challenge = data.get("challenge")
                print(f"🔐 URL verification: {challenge}")
                return PlainTextResponse(challenge)
            
            event_type = data.get('event', {}).get('type', 'unknown')
            event_ts = data.get('event', {}).get('ts', 'no_ts')
            print(f"📨 Event: {event_type} at {event_ts}")
            
        except json.JSONDecodeError:
            print("📨 Could not parse JSON body")
        
        return await handler.handle(request)
        
    except Exception as e:
        print(f"❌ Slack events error: {e}")
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/health")
async def health_check():
    return {
        "status": "healthy",
        "bot_token_valid": SLACK_BOT_TOKEN.startswith("xoxb-"),
        "signing_secret_length": len(SLACK_SIGNING_SECRET),
        "processed_events": len(processed_events),
        "active_tasks": len(processing_tasks),
        "cached_responses": len(sent_responses)
    }

@app.get("/debug")
async def debug_info():
    return {
        "processed_events_count": len(processed_events),
        "processing_tasks": processing_tasks,
        "sent_responses_count": len(sent_responses),
        "recent_events": list(processed_events)[-5:],
        "recent_responses": {k: f"{time.time() - v:.1f}s ago" for k, v in list(sent_responses.items())[-3:]}
    }

@app.post("/clear-cache")
async def clear_cache():
    global processed_events, processing_tasks, sent_responses
    
    counts = {
        "processed_events": len(processed_events),
        "processing_tasks": len(processing_tasks),
        "sent_responses": len(sent_responses)
    }
    
    processed_events.clear()
    processing_tasks.clear()
    sent_responses.clear()
    
    return {"message": "Caches cleared", "cleared_counts": counts}

@app.get("/")
async def root():
    return {
        "message": "Slack Bot with Enhanced Deduplication",
        "status": "running",
        "features": ["Event deduplication", "Response deduplication", "Async processing"]
    }

if __name__ == "__main__":
    print("🚀 Starting Enhanced Slack Bot...")
    print(f"✅ Bot token format: {'Valid' if SLACK_BOT_TOKEN.startswith('xoxb-') else 'INVALID'}")
    print(f"✅ Signing secret length: {len(SLACK_SIGNING_SECRET)} ({'Valid' if len(SLACK_SIGNING_SECRET) == 32 else 'Check length'})")
    print("🛡️  Deduplication: Event + Response")
    
    uvicorn.run(
        app,
        host="0.0.0.0",
        port=3002,
        reload=False,
        workers=1,
        access_log=True
    )