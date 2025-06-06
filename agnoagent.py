import asyncio
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
from typing import Optional
import io
from contextlib import redirect_stdout
from datetime import datetime
from agno.agent import Agent
from agno.models.openai import OpenAIChat
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
                model=OpenAIChat(
                    id="gpt-4.1",
                    api_key="sk-svcacct-wkLQxAy8QRFruigby2JpaPgnqI53U1HcS8_6wSs8RbbC6idCO_T3BlbkFJCKjO5UlgN-TUF9wB-6UdG2hjaaenZq8sdfV-cuADdTRtp0SC4A"
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
    
    print("🎯 Starting FastAPI Agent Server...")
    print(f"Server URL: {SERVER_URL}")
    
    # Run the FastAPI server
    uvicorn.run(
        "agnoagent:app",  # Change "main" to your filename if different
        host="0.0.0.0",
        port=8000,
        reload=True
    )