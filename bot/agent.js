// ai_logic.js

import { ChatOpenAI } from "@langchain/openai";
import { ChatPromptTemplate } from "@langchain/core/prompts";
import { DynamicTool } from "@langchain/core/tools";
import { AgentExecutor, createOpenAIToolsAgent } from "langchain/agents";
import { AIMessage, HumanMessage } from "@langchain/core/messages";
import { v4 as uuidv4 } from 'uuid';

const OPENAI_API_KEY = process.env.OPENAI_API_KEY;
const SLACK_NOTIFICATION_CHANNEL_ID = process.env.SLACK_NOTIFICATION_CHANNEL_ID;

// This object acts as a "filing cabinet" for user sessions.
// The key is the userId, and the value is their chat history array.
const userHistories = {};

/**
 * Processes a user's message using an AI agent with memory.
 * @param {string} userInput - The text of the user's message.
 * @param {string} userId - The unique ID of the user sending the message.
 * @param {string} userName - The display name of the user.
 * @param {object} slackClient - The Slack client instance for API calls.
 * @returns {Promise<string>} The AI's final text response.
 */
export async function processMessage(userInput, userId, userName, slackClient) {

  // --- AI Tools ---
  // These are the specific functions the AI agent can decide to use.
  const tools = [
    new DynamicTool({
      name: "get_all_rooms",
      description: "Get a list of all available meeting rooms.",
      func: async () => JSON.stringify([{ id: "1", name: "Red Room" }, { id: "2", name: "Blue Room" }]),
    }),
    new DynamicTool({
      name: "get_available_rooms",
      description: "Find available rooms for a specific date and time.",
      func: async ({ date, start_time, end_time }) => JSON.stringify([{ id: "2", name: "Blue Room", available: true }]),
    }),
    new DynamicTool({
      name: "book_room",
      description: "Book a meeting room for a user. The 'name' parameter should be the user's full name.",
      func: async ({ room_id, date, start_time, end_time }) => {
        console.log(`Booking room ${room_id} for ${userName}`);
        const bookingId = uuidv4();
        
        if (SLACK_NOTIFICATION_CHANNEL_ID && slackClient) {
            const message = `🔔 *New Booking Notification* 🔔\n*Room ID:* ${room_id}\n*Booked By:* ${userName}\n*When:* ${date} from ${start_time} to ${end_time}`;
            await slackClient.chat.postMessage({
                channel: SLACK_NOTIFICATION_CHANNEL_ID,
                text: message,
            });
        }
        return JSON.stringify({ success: true, booking_id: bookingId, room_name: `Room ${room_id}` });
      },
    }),
    new DynamicTool({
      name: "get_room_bookings",
      description: "Get the booking schedule for a specific room on a given date.",
      func: async ({ room_id, date }) => JSON.stringify([{ booking_id: "xyz-123", start_time: "14:00", end_time: "15:00" }]),
    }),
    new DynamicTool({
      name: "cancel_booking",
      description: "Cancel an existing booking using its booking ID.",
      func: async ({ booking_id }) => {
          console.log(`Canceling booking ${booking_id}`);
          return JSON.stringify({ success: true, message: `Booking ${booking_id} has been canceled.` });
      },
    }),
  ];
  
  // --- LLM and Agent Prompt ---
  const llm = new ChatOpenAI({ modelName: "gpt-4-turbo", temperature: 0, apiKey: OPENAI_API_KEY });

  const prompt = ChatPromptTemplate.fromMessages([
    ["system", `You are a helpful meeting room booking assistant.
      - The current date is ${new Date().toISOString().split('T')[0]}.
      - The user's name is '${userName}'.
      - these are the rooms and their room id [
        { "id": 1, "name": "denali" },
        { "id": 2, "name": "cherry blossom" },
        { "id": 3, "name": "peony" },
        { "id": 4, "name": "lilac" },
        { "id": 5, "name": "iris" }
      ] always ask the user for room name start date and end date`],
    ["placeholder", "{chat_history}"],
    ["human", "{input}"],
    ["placeholder", "{agent_scratchpad}"],
  ]);

  // Create the agent and the executor which runs the main loop
  const agent = await createOpenAIToolsAgent({ llm, tools, prompt });
  const agentExecutor = new AgentExecutor({ agent, tools });

  // Get the specific history for this user, or create it if it's their first message.
  if (!userHistories[userId]) {
    userHistories[userId] = [];
  }
  const currentUserHistory = userHistories[userId];

  // Add the new user message to their specific history
  currentUserHistory.push(new HumanMessage(userInput));

  // Invoke the agent with the input and the user's specific chat history
  const result = await agentExecutor.invoke({
    input: userInput,
    chat_history: currentUserHistory,
  });

  // Add the AI's response to their history to remember it for the next turn
  currentUserHistory.push(new AIMessage(result.output));

  // Return the final text response
  return result.output;
}