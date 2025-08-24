
import dotenv from 'dotenv';
import bolt from '@slack/bolt'; // Import the entire bolt library
import { processMessage } from './agent.js'; // Assuming agent.js is also using ES modules

// Destructure App and SocketModeReceiver from the bolt package
const { App, SocketModeReceiver } = bolt;

// Load environment variables from .env file
// This should be called only once at the beginning
dotenv.config();

// --- Configuration ---
// Retrieve Slack tokens from environment variables
const SLACK_BOT_TOKEN = process.env.SLACK_BOT_TOKEN;
const SLACK_APP_TOKEN = process.env.SLACK_APP_TOKEN;
const SLACK_SIGNING_SECRET = process.env.SLACK_SIGNING_SECRET; // Ensure this is also in your .env

// --- Initialize the Slack App with SocketModeReceiver ---
// Explicitly create the SocketModeReceiver for clarity and best practice
const socketModeReceiver = new SocketModeReceiver({
  appToken: SLACK_APP_TOKEN, // Use the app token for Socket Mode connection
  // Optional: If you plan to use OAuth for distribution, uncomment and configure these:
  // clientId: process.env.CLIENT_ID,
  // clientSecret: process.env.CLIENT_SECRET,
  // stateSecret: process.env.STATE_SECRET,
  // scopes: ['channels:read', 'chat:write', 'app_mentions:read', 'channels:manage', 'commands'],
});

// Initialize the Bolt App using the explicitly created receiver
const app = new App({
  receiver: socketModeReceiver, // Pass the explicit receiver
  token: SLACK_BOT_TOKEN,      // Bot token for API calls
  signingSecret: SLACK_SIGNING_SECRET, // Signing secret for request verification
});

// --- Slack Event Handlers ---
// Listen for 'message' events in direct messages
app.event('message', async ({ event, client, logger }) => {
  // Ignore messages from bots, messages in threads, or messages not in direct messages
  if (event.bot_id || event.thread_ts || event.channel_type !== 'im') {
    return;
  }

  const userId = event.user;
  const userInput = event.text;

  try {
    // Add a 'thinking_face' reaction to indicate processing
    await client.reactions.add({ name: 'thinking_face', channel: event.channel, timestamp: event.ts });

    // Get the user's profile information to retrieve their real name
    const profile = await client.users.profile.get({ user: userId });
    const userName = profile.profile.real_name || "Unknown User";

    logger.info(`Processing message for user ${userName} (${userId})`);

    // Process the message using the external agent.js function
    const response = await processMessage(userInput, userId, userName, client);

    logger.info(`Received response for user ${userId}: "${response}"`);

    // Send the AI's response back to Slack
    await client.chat.postMessage({
      channel: event.channel,
      text: response,
    });

    // Remove the 'thinking_face' reaction after sending the response
    await client.reactions.remove({ name: 'thinking_face', channel: event.channel, timestamp: event.ts });
  } catch (error) {
    // Log and handle any errors during message processing
    logger.error(`Error processing message for user ${userId}:`, error);
    await client.reactions.remove({ name: 'thinking_face', channel: event.channel, timestamp: event.ts });
    await client.chat.postMessage({
      channel: event.channel,
      text: "Sorry, I ran into an internal error. Please try again."
    });
  }
});

// --- Start the Bot ---
// Immediately invoked async function to start the Slack app
(async () => {
  await app.start();
  console.log("🤖 AI Slack bot is running in Socket Mode...");
  app.logger.info('⚡️ Bolt app started'); // Log from the Bolt app's logger
})();
