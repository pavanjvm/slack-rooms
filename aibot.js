require('dotenv').config();
const { App } = require("@slack/bolt");
const { ChatOpenAI } = require("@langchain/openai");
const { ChatPromptTemplate } = require("@langchain/core/prompts");
const { AgentExecutor, createOpenAIToolsAgent } = require("langchain/agents");
const { DynamicTool } = require("@langchain/core/tools");
const { McpClient } = require("@modelcontextprotocol/sdk/client/mcp.js");
const { StreamableHTTPClientTransport } = require("@modelcontextprotocol/sdk/client/streamableHttp.js");

// --- Configuration ---
const SLACK_BOT_TOKEN = process.env.SLACK_BOT_TOKEN;
const SLACK_APP_TOKEN = process.env.SLACK_APP_TOKEN;
const OPENAI_API_KEY = process.env.OPENAI_API_KEY;
const SLACK_NOTIFICATION_CHANNEL_ID = process.env.SLACK_NOTIFICATION_CHANNEL_ID; // Add this to your .env file

// Correct URL for your MCP server running on port 3001
const MCP_SERVER_URL = "http://localhost:3001/mcp";

// --- Initialize the Slack App ---
const app = new App({
  token: SLACK_BOT_TOKEN,
  socketMode: true,
  appToken: SLACK_APP_TOKEN,
});

// --- MCP Client Wrapper ---
// This class will manage the connection and tool calls to your MCP server.
class McpToolClient {
  constructor(url) {
    this.url = url;
    this.mcpClient = null;
    this.transport = null;
  }

  // Initialize the connection to the MCP server
  async initialize() {
    this.transport = new StreamableHTTPClientTransport(this.url);
    this.mcpClient = new McpClient();
    await this.mcpClient.connect(this.transport);
    console.log("✅ MCP Client Initialized. Session ID:", this.transport.sessionId);
  }

  // Generic function to call any tool on the MCP server
  async callTool(toolName, params) {
    if (!this.mcpClient) {
      throw new Error("MCP Client not initialized.");
    }
    console.log(`📞 Calling MCP tool: ${toolName} with params:`, params);
    try {
      const response = await this.mcpClient.tool(toolName, params);
      console.log(`✅ MCP Response for ${toolName}:`, response);
      if (response.content && response.content[0] && response.content[0].text) {
        return JSON.parse(response.content[0].text);
      }
      return response;
    } catch (error) {
      console.error(`❌ Error calling MCP tool ${toolName}:`, error);
      const errorMessage = error.message || "An unknown error occurred with the tool.";
      return { error: errorMessage };
    }
  }
}

// --- LangChain Agent Setup ---
// In-memory store for conversation history and MCP sessions per user
const userSessions = {};

// The `client` object is now passed in to allow tools to post messages
async function createAgentAndExecutor(mcpClient, userName, client, logger) {
  const tools = [
    new DynamicTool({
      name: "get_all_rooms",
      description: "Get a list of all available meeting rooms.",
      func: async () => mcpClient.callTool("get_all_rooms", {}),
    }),
    new DynamicTool({
      name: "get_available_rooms",
      description: "Find available rooms for a specific date and time. Use today's date if not specified.",
      func: async (input) => mcpClient.callTool("get_available_rooms", input),
    }),
    new DynamicTool({
      name: "book_room",
      description: "Book a meeting room for a user. The 'name' parameter should be the user's full name.",
      func: async (input) => {
        const bookingData = { ...input, name: userName };
        const result = await mcpClient.callTool("book_room", bookingData);

        // If booking is successful, post a notification
        if (result.success && SLACK_NOTIFICATION_CHANNEL_ID) {
          try {
            const { room_name, booked_by, booking_details } = result;
            const message = `🔔 *New Booking Notification* 🔔\n*Room:* ${room_name}\n*Booked By:* ${booked_by}\n*When:* ${booking_details.date} from ${booking_details.start_time} to ${booking_details.end_time}`;
            
            await client.chat.postMessage({
              channel: SLACK_NOTIFICATION_CHANNEL_ID,
              text: message,
            });
            logger.info(`✅ Posted booking notification to channel ${SLACK_NOTIFICATION_CHANNEL_ID}`);
          } catch (error) {
            logger.error(`❌ Failed to post notification to channel ${SLACK_NOTIFICATION_CHANNEL_ID}:`, error);
          }
        }
        return result;
      },
    }),
    new DynamicTool({
      name: "get_room_bookings",
      description: "Get the booking schedule for a specific room on a given date.",
      func: async (input) => mcpClient.callTool("get_room_bookings", input),
    }),
    new DynamicTool({
        name: "cancel_booking",
        description: "Cancel an existing booking using its booking ID.",
        func: async (input) => mcpClient.callTool("cancel_booking", input),
    }),
  ];

  const llm = new ChatOpenAI({
    modelName: "gpt-4-turbo",
    temperature: 0,
    apiKey: OPENAI_API_KEY,
  });

  const prompt = ChatPromptTemplate.fromMessages([
    ["system", `You are a helpful meeting room booking assistant.
    - Today's date is ${new Date().toISOString().split('T')[0]}.
    - Your goal is to help the user book, find, or manage meeting rooms.
    - When booking a room, you MUST use the 'book_room' tool. The user's name is '${userName}'.
    - Be conversational and friendly.
    - When you get a successful booking confirmation, tell the user the booking ID.`],
    ["placeholder", "{chat_history}"],
    ["human", "{input}"],
    ["placeholder", "{agent_scratchpad}"],
  ]);

  const agent = await createOpenAIToolsAgent({ llm, tools, prompt });
  const agentExecutor = new AgentExecutor({ agent, tools });

  return agentExecutor;
}

// --- Slack Event Handlers ---

app.event('message', async ({ event, client, logger }) => {
  if (event.bot_id || event.thread_ts || event.channel_type !== 'im') {
    return;
  }

  const userId = event.user;
  const userInput = event.text;

  try {
    await client.reactions.add({ name: 'thinking_face', channel: event.channel, timestamp: event.ts });

    if (!userSessions[userId]) {
      logger.info(`New user session for ${userId}. Initializing MCP Client.`);
      const mcpClient = new McpToolClient(MCP_SERVER_URL);
      await mcpClient.initialize();
      
      const profile = await client.users.profile.get({ user: userId });
      const userName = profile.profile.real_name || "Unknown User";

      // Pass the client and logger to the agent creator
      const agentExecutor = await createAgentAndExecutor(mcpClient, userName, client, logger);

      userSessions[userId] = {
        agentExecutor,
        userName,
        history: [],
      };
    }

    const session = userSessions[userId];

    logger.info(`Invoking agent for user ${userId} with input: "${userInput}"`);
    
    const result = await session.agentExecutor.invoke({
      input: userInput,
      chat_history: session.history,
    });

    logger.info(`Agent output for user ${userId}: "${result.output}"`);

    session.history.push({ role: 'human', content: userInput });
    session.history.push({ role: 'ai', content: result.output });
    if (session.history.length > 10) {
        session.history = session.history.slice(-10);
    }

    await client.chat.postMessage({
      channel: event.channel,
      text: result.output,
    });

     await client.reactions.remove({ name: 'thinking_face', channel: event.channel, timestamp: event.ts });

  } catch (error) {
    logger.error(`Error processing message for user ${userId}:`, error);
    await client.reactions.remove({ name: 'thinking_face', channel: event.channel, timestamp: event.ts });
    await client.chat.postMessage({
        channel: event.channel,
        text: "Sorry, I ran into an internal error. Please try again in a moment."
    });
  }
});


// --- Start the Bot ---
(async () => {
  if (!OPENAI_API_KEY) {
      console.error("❌ OPENAI_API_KEY environment variable is not set.");
      process.exit(1);
  }
  if (!SLACK_NOTIFICATION_CHANNEL_ID) {
      console.warn("⚠️ SLACK_NOTIFICATION_CHANNEL_ID is not set. Booking notifications will not be sent.");
  }
  await app.start();
  console.log("🤖 AI Slack bot is running in Socket Mode...");
})();