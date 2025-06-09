# Meeting Room Booking System

This is a **Meeting Room Booking System** that lets users book conference rooms through natural language conversations in Slack.

## Architecture

The system uses a 4-layer architecture:

```
Slack Bot → AI Agent → MCP Server → Supabase Database
```

- **Slack Bot** (Python/Flask, Port 3002): Handles Slack mentions and events
- **AI Agent** (Python/FastAPI, Port 8000): Processes natural language using Google Gemini
- **MCP Server** (Node.js, Port 3001): Manages room bookings and database operations
- **Supabase**: PostgreSQL database storing rooms and bookings

## How to Run

### Prerequisites
- Node.js, Python 3.8+
- Supabase account
- Google Gemini API key
- Slack bot token

### Quick Start

1. **Start MCP Server** (must be first):
   ```bash
   cd mcp-server
   npm install
   node dist/index.js  # Port 3001
   ```

2. **Start AI Agent**:
   ```bash
   python3 agnoagent.py  # Port 8000
   ```

3. **Start Slack Bot**:
   ```bash
   python3 bot.py  # Port 3002
   ```

### Usage

Just mention your bot in Slack with natural language:

```
@bot book room1 tomorrow 2pm to 4pm for John
@bot is room2 available today 10am?
```

The system automatically handles time validation, conflict detection, and provides conversational responses through the AI agent.
