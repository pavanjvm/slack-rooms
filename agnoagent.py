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
from agno.storage.sqlite import SqliteStorage
from agno.memory.v2.db.sqlite import SqliteMemoryDb
from agno.memory.v2.memory import Memory
from agno.memory.v2.manager import MemoryManager
import os

os.makedirs("tmp", exist_ok=True)

agent_storage = SqliteStorage(
    table_name="agent_sessions", 
    db_file="tmp/persistent_memory.db"
)
memory_db = SqliteMemoryDb(
    table_name="memory", 
    db_file="tmp/memory.db"
)

# Configuration
SERVER_URL = "http://localhost:3001/mcp"
GEMINI_API_KEY = ""

# FastAPI app instance
app = FastAPI(title="Agent API", description="API for MCP Agent interactions")

memory = Memory(
    db=memory_db,
    memory_manager=MemoryManager(
        memory_capture_instructions="""\
            Maintain conversation context and history
        """,
        model=Gemini(
            id="gemini-1.5-pro-002",
            api_key=GEMINI_API_KEY
        ),
    ),
)

# Request model
class PromptRequest(BaseModel):
    message: str

# Response model
class AgentResponse(BaseModel):
    response: str
    success: bool
    error: Optional[str] = None

# Custom datetime tool
def get_current_datetime() -> str:
    """Get the current date and time in a readable format"""
    now = datetime.now()
    return now.strftime("%Y-%m-%d %H:%M:%S (%A)")

# Create toolkit with datetime tool
datetime_toolkit = Toolkit()
datetime_toolkit.register(get_current_datetime)

# Global agent instance - will be initialized on startup
agent = None
mcp_tools = None

async def initialize_agent():
    """Initialize the agent and MCP tools once on startup"""
    global agent, mcp_tools
    
    try:
        print("🔄 Initializing MCP tools and agent...")
        
        # Initialize MCP tools connection
        mcp_tools = MCPTools(transport="streamable-http", url=SERVER_URL)
        await mcp_tools.__aenter__()  # Manually enter the context
        
        # Create the agent with persistent tools
        agent = Agent(
            model=Gemini(
                id="gemini-1.5-pro-002",  # High context, very good model
                api_key=GEMINI_API_KEY
            ),
            tools=[mcp_tools, datetime_toolkit],
            show_tool_calls=False,
            markdown=False,
            memory=memory,
            add_history_to_messages=True,
            num_history_responses=3,
            enable_user_memories=True,
            instructions=[
                "You are a meeting room booking agent.these are the room names with their room ids [denali:4, cherry blossom:5, donee:1, some room:2, lilac:3, Peony:6] there could be some typo find the best matching room from this list",
                "IMPORTANT: Always check the current date and time first using get_current_datetime() before processing any booking request.",
                "You can only book meetings for future dates and times. Do not allow bookings for past dates or times.",
                "If no date is specified, use today's date but ensure the time is in the future.",
                "If a user tries to book a meeting in the past, politely decline and suggest future time slots.",
                "always reply with the username like hi <username> and then reply message",
                "When processing booking requests,always make a tool call to check the booked slots of that particular room to make sure the current booking time doesnt conflict with previous bookings and validate that the requested date/time is after the current date/time and always ask for name if not provided. make sure to do with least tool calls"
            ]
        )
        
        print("✅ Agent and MCP tools initialized successfully!")
        
    except Exception as e:
        print(f"❌ Failed to initialize agent: {e}")
        raise e

async def cleanup_agent():
    """Cleanup the agent and MCP tools on shutdown"""
    global mcp_tools
    
    try:
        if mcp_tools:
            await mcp_tools.__aexit__(None, None, None)  # Manually exit the context
            print("✅ MCP tools cleaned up successfully!")
    except Exception as e:
        print(f"❌ Error during cleanup: {e}")

async def run_agent(message: str) -> str:
    """Run agent with the persistent agent instance"""
    global agent
    
    if agent is None:
        raise HTTPException(status_code=500, detail="Agent not initialized")
    
    try:
        print(f"\n=== AGENT REQUEST ===")
        print(f"Message: {message}")
        
        # Capture output
        output_buffer = io.StringIO()
        
        # Use the persistent agent instance
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

# FastAPI event handlers
@app.on_event("startup")
async def startup_event():
    """Initialize the agent on application startup"""
    await initialize_agent()

@app.on_event("shutdown")
async def shutdown_event():
    """Cleanup the agent on application shutdown"""
    await cleanup_agent()

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
        reload=False,
        workers=1
    )