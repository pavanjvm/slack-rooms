#!/usr/bin/env node
"use strict";
var __importDefault = (this && this.__importDefault) || function (mod) {
    return (mod && mod.__esModule) ? mod : { "default": mod };
};
Object.defineProperty(exports, "__esModule", { value: true });
const express_1 = __importDefault(require("express"));
const node_crypto_1 = require("node:crypto");
const mcp_js_1 = require("@modelcontextprotocol/sdk/server/mcp.js");
const streamableHttp_js_1 = require("@modelcontextprotocol/sdk/server/streamableHttp.js");
// Remove the problematic import - we'll create a simple in-memory store instead
// import { InMemoryEventStore } from "@modelcontextprotocol/sdk/server/inMemory.js";
const zod_1 = require("zod");
const supabase_js_1 = require("@supabase/supabase-js");
const dotenv_1 = __importDefault(require("dotenv"));
// Load environment variables
dotenv_1.default.config();
// Supabase configuration
const supabaseUrl = 'https://mlqoofzdxkoqoiologmf.supabase.co';
const supabaseKey = "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJpc3MiOiJzdXBhYmFzZSIsInJlZiI6Im1scW9vZnpkeGtvcW9pb2xvZ21mIiwicm9sZSI6ImFub24iLCJpYXQiOjE3NDg5NDM5NjQsImV4cCI6MjA2NDUxOTk2NH0.DFcGsIlEzXsEOIsYVw2jXlwYSNRE-_Eo2PlgOs3AwlE";
const supabase = (0, supabase_js_1.createClient)(supabaseUrl, supabaseKey);
// Simple in-memory event store implementation that matches the EventStore interface
class SimpleEventStore {
    constructor() {
        this.events = [];
        this.messageCounter = 0;
    }
    async storeEvent(streamId, message) {
        const messageId = `msg_${this.messageCounter++}`;
        this.events.push({ streamId, messageId, message });
        return messageId;
    }
    async replayEventsAfter(streamId, messageId) {
        const startIndex = this.events.findIndex(event => event.streamId === streamId && event.messageId === messageId);
        if (startIndex === -1) {
            // If messageId not found, return all events for this stream
            return this.events
                .filter(event => event.streamId === streamId)
                .map(event => event.message);
        }
        // Return events after the specified messageId
        return this.events
            .slice(startIndex + 1)
            .filter(event => event.streamId === streamId)
            .map(event => event.message);
    }
}
// Utility function to check if request is an initialize request
function isInitializeRequest(body) {
    return body && body.method === 'initialize';
}
class MeetingRoomServer {
    constructor(port = 3000) {
        this.transports = {};
        this.port = port;
        this.app = (0, express_1.default)();
        this.app.use(express_1.default.json());
        this.setupRoutes();
    }
    createServer() {
        const server = new mcp_js_1.McpServer({
            name: "meeting-room-server",
            version: "0.4.0"
        });
        this.setupTools(server);
        return server;
    }
    setupTools(server) {
        // Get available rooms tool
        server.tool("get_available_rooms", {
            date: zod_1.z.string().describe("Date in YYYY-MM-DD format"),
            start_time: zod_1.z.string().describe("Start time in HH:MM format (24-hour)"),
            end_time: zod_1.z.string().describe("End time in HH:MM format (24-hour)")
        }, async ({ date, start_time, end_time }) => {
            const startDateTime = `${date}T${start_time}:00`;
            const endDateTime = `${date}T${end_time}:00`;
            try {
                // Get all rooms
                const { data: rooms, error: roomsError } = await supabase
                    .from('rooms')
                    .select('*')
                    .order('name');
                if (roomsError)
                    throw roomsError;
                // Get conflicting bookings
                const { data: bookings, error: bookingsError } = await supabase
                    .from('bookings')
                    .select('room_id')
                    .eq('status', 'confirmed')
                    .lt('start_time', endDateTime)
                    .gt('end_time', startDateTime);
                if (bookingsError)
                    throw bookingsError;
                const bookedRoomIds = new Set(bookings?.map(b => b.room_id) || []);
                const availableRooms = rooms?.filter(room => !bookedRoomIds.has(room.id)) || [];
                return {
                    content: [{
                            type: "text",
                            text: JSON.stringify({
                                available_rooms: availableRooms,
                                total_available: availableRooms.length,
                                requested_time: `${date} ${start_time} - ${end_time}`
                            }, null, 2)
                        }]
                };
            }
            catch (error) {
                throw new Error(`Failed to get available rooms: ${error instanceof Error ? error.message : String(error)}`);
            }
        });
        // Book room tool
        server.tool("book_room", {
            room_id: zod_1.z.number().describe("ID of the room to book"),
            date: zod_1.z.string().describe("Date in YYYY-MM-DD format"),
            start_time: zod_1.z.string().describe("Start time in HH:MM format (24-hour)"),
            end_time: zod_1.z.string().describe("End time in HH:MM format (24-hour)")
        }, async ({ room_id, date, start_time, end_time }) => {
            const startDateTime = `${date}T${start_time}:00`;
            const endDateTime = `${date}T${end_time}:00`;
            try {
                if (!room_id) {
                    throw new Error("room_id is required");
                }
                // Get room details
                const { data: room, error: roomError } = await supabase
                    .from('rooms')
                    .select('*')
                    .eq('id', room_id)
                    .single();
                if (roomError || !room) {
                    throw new Error(`Room with ID ${room_id} not found`);
                }
                // Check for conflicts
                const { data: conflicts, error: conflictError } = await supabase
                    .from('bookings')
                    .select('*')
                    .eq('room_id', room_id)
                    .eq('status', 'confirmed')
                    .lt('start_time', endDateTime)
                    .gt('end_time', startDateTime);
                if (conflictError)
                    throw conflictError;
                if (conflicts && conflicts.length > 0) {
                    throw new Error(`Room '${room.name}' is already booked during the requested time`);
                }
                // Create booking
                const { data: booking, error: bookingError } = await supabase
                    .from('bookings')
                    .insert([{
                        room_id: room_id,
                        start_time: startDateTime,
                        end_time: endDateTime,
                        status: 'confirmed'
                    }])
                    .select()
                    .single();
                if (bookingError)
                    throw bookingError;
                return {
                    content: [{
                            type: "text",
                            text: JSON.stringify({
                                success: true,
                                booking_id: booking.id,
                                room_name: room.name,
                                booking_details: {
                                    date,
                                    start_time,
                                    end_time,
                                    status: 'confirmed'
                                },
                                message: `Room '${room.name}' successfully booked for ${date} from ${start_time} to ${end_time}`
                            }, null, 2)
                        }]
                };
            }
            catch (error) {
                throw new Error(`Failed to book room: ${error instanceof Error ? error.message : String(error)}`);
            }
        });
        // Get room bookings tool
        server.tool("get_room_bookings", {
            room_id: zod_1.z.number().describe("ID of the room"),
            date: zod_1.z.string().describe("Date in YYYY-MM-DD format")
        }, async ({ room_id, date }) => {
            const startOfDay = `${date}T00:00:00`;
            const endOfDay = `${date}T23:59:59`;
            try {
                const { data: bookings, error } = await supabase
                    .from('bookings')
                    .select(`
            *,
            rooms!inner(name)
          `)
                    .eq('room_id', room_id)
                    .eq('status', 'confirmed')
                    .gte('start_time', startOfDay)
                    .lte('start_time', endOfDay)
                    .order('start_time');
                if (error)
                    throw error;
                return {
                    content: [{
                            type: "text",
                            text: JSON.stringify({
                                room_id,
                                date,
                                bookings: bookings || [],
                                total_bookings: bookings?.length || 0
                            }, null, 2)
                        }]
                };
            }
            catch (error) {
                throw new Error(`Failed to get room bookings: ${error instanceof Error ? error.message : String(error)}`);
            }
        });
        // Cancel booking tool
        server.tool("cancel_booking", {
            booking_id: zod_1.z.number().describe("ID of the booking to cancel")
        }, async ({ booking_id }) => {
            try {
                // Get booking details first
                const { data: booking, error: getError } = await supabase
                    .from('bookings')
                    .select(`
            *,
            rooms!inner(name)
          `)
                    .eq('id', booking_id)
                    .single();
                if (getError || !booking) {
                    throw new Error(`Booking with ID ${booking_id} not found`);
                }
                // Cancel the booking
                const { error: updateError } = await supabase
                    .from('bookings')
                    .update({ status: 'cancelled' })
                    .eq('id', booking_id);
                if (updateError)
                    throw updateError;
                return {
                    content: [{
                            type: "text",
                            text: JSON.stringify({
                                success: true,
                                message: "Booking cancelled successfully",
                                cancelled_booking: {
                                    booking_id,
                                    room_name: booking.rooms.name,
                                    start_time: booking.start_time,
                                    end_time: booking.end_time
                                }
                            }, null, 2)
                        }]
                };
            }
            catch (error) {
                throw new Error(`Failed to cancel booking: ${error instanceof Error ? error.message : String(error)}`);
            }
        });
        // Get all rooms tool
        server.tool("get_all_rooms", {}, async () => {
            try {
                const { data: rooms, error } = await supabase
                    .from('rooms')
                    .select('*')
                    .order('name');
                if (error)
                    throw error;
                return {
                    content: [{
                            type: "text",
                            text: JSON.stringify({
                                rooms: rooms || [],
                                total_rooms: rooms?.length || 0
                            }, null, 2)
                        }]
                };
            }
            catch (error) {
                throw new Error(`Failed to get rooms: ${error instanceof Error ? error.message : String(error)}`);
            }
        });
    }
    setupRoutes() {
        // Handle POST requests for client-to-server communication
        this.app.post('/mcp', async (req, res) => {
            console.log('Received MCP request:', req.body);
            try {
                // Check for existing session ID
                const sessionId = req.headers['mcp-session-id'];
                let transport;
                if (sessionId && this.transports[sessionId]) {
                    // Reuse existing transport
                    transport = this.transports[sessionId];
                }
                else if (!sessionId && isInitializeRequest(req.body)) {
                    // New initialization request - without event store for simplicity
                    transport = new streamableHttp_js_1.StreamableHTTPServerTransport({
                        sessionIdGenerator: () => (0, node_crypto_1.randomUUID)(),
                        // Removed eventStore to avoid interface compatibility issues
                        onsessioninitialized: (sessionId) => {
                            // Store the transport by session ID
                            this.transports[sessionId] = transport;
                            console.log(`New MCP session initialized: ${sessionId}`);
                        }
                    });
                    // Clean up transport when closed
                    transport.onclose = () => {
                        if (transport.sessionId) {
                            delete this.transports[transport.sessionId];
                            console.log(`MCP session closed: ${transport.sessionId}`);
                        }
                    };
                    const server = this.createServer();
                    // Connect to the MCP server
                    await server.connect(transport);
                }
                else {
                    // Invalid request
                    res.status(400).json({
                        jsonrpc: '2.0',
                        error: {
                            code: -32000,
                            message: 'Bad Request: No valid session ID provided',
                        },
                        id: null,
                    });
                    return;
                }
                // Handle the request
                await transport.handleRequest(req, res, req.body);
            }
            catch (error) {
                console.error('Error handling MCP request:', error);
                if (!res.headersSent) {
                    res.status(500).json({
                        jsonrpc: '2.0',
                        error: {
                            code: -32603,
                            message: 'Internal server error',
                        },
                        id: null,
                    });
                }
            }
        });
        // Reusable handler for GET and DELETE requests
        const handleSessionRequest = async (req, res) => {
            const sessionId = req.headers['mcp-session-id'];
            if (!sessionId || !this.transports[sessionId]) {
                res.status(400).send('Invalid or missing session ID');
                return;
            }
            const transport = this.transports[sessionId];
            await transport.handleRequest(req, res);
        };
        // Handle GET requests for server-to-client notifications via SSE
        this.app.get('/mcp', handleSessionRequest);
        // Handle DELETE requests for session termination
        this.app.delete('/mcp', handleSessionRequest);
        // Health check endpoint
        this.app.get('/health', (req, res) => {
            res.json({
                status: 'healthy',
                server: 'meeting-room-mcp-server',
                version: '0.4.0',
                active_sessions: Object.keys(this.transports).length
            });
        });
        // Root endpoint with server info
        this.app.get('/', (req, res) => {
            res.json({
                name: 'Meeting Room MCP Server',
                version: '0.4.0',
                description: 'MCP server for managing meeting room bookings',
                mcp_endpoint: '/mcp',
                health_endpoint: '/health',
                transport: 'Streamable HTTP',
                tools: [
                    'get_available_rooms',
                    'book_room',
                    'get_room_bookings',
                    'cancel_booking',
                    'get_all_rooms'
                ]
            });
        });
    }
    async run() {
        // Test Supabase connection first
        try {
            const { data, error } = await supabase
                .from('rooms')
                .select('count', { count: 'exact', head: true });
            if (error)
                throw error;
            console.log("✅ Successfully connected to Supabase database.");
        }
        catch (err) {
            console.error("❌ Failed to connect to Supabase:", err);
            process.exit(1);
        }
        // Start the Express server
        try {
            const server = this.app.listen(this.port, () => {
                console.log(`🚀 Meeting Room MCP Server running on http://localhost:${this.port}`);
                console.log(`📋 MCP endpoint: http://localhost:${this.port}/mcp`);
                console.log(`❤️  Health check: http://localhost:${this.port}/health`);
                console.log(`📖 Server info: http://localhost:${this.port}/`);
            });
            // Handle server errors
            server.on('error', (err) => {
                console.error('❌ Server error:', err);
                if (err.code === 'EADDRINUSE') {
                    console.error(`❌ Port ${this.port} is already in use. Try a different port.`);
                }
                process.exit(1);
            });
            // Handle uncaught exceptions
            process.on('uncaughtException', (err) => {
                console.error('❌ Uncaught Exception:', err);
                process.exit(1);
            });
            // Handle unhandled promise rejections
            process.on('unhandledRejection', (reason, promise) => {
                console.error('❌ Unhandled Rejection at:', promise, 'reason:', reason);
                process.exit(1);
            });
            // Graceful shutdown
            process.on('SIGINT', () => {
                console.log('\n🛑 Shutting down MCP server...');
                // Close all active transports
                Object.values(this.transports).forEach(transport => {
                    if (transport.sessionId) {
                        console.log(`Closing session: ${transport.sessionId}`);
                    }
                });
                server.close(() => {
                    console.log('✅ Server closed gracefully');
                    process.exit(0);
                });
            });
            process.on('SIGTERM', () => {
                console.log('\n🛑 Received SIGTERM, shutting down gracefully...');
                server.close(() => {
                    console.log('✅ Server closed gracefully');
                    process.exit(0);
                });
            });
        }
        catch (err) {
            console.error('❌ Failed to start server:', err);
            process.exit(1);
        }
    }
}
// Allow port to be specified via environment variable
const port = process.env.PORT ? parseInt(process.env.PORT) : 3001;
const server = new MeetingRoomServer(port);
// Add comprehensive error handling for startup
server.run().catch((error) => {
    console.error('❌ Fatal error starting MCP server:', error);
    console.error('Stack trace:', error.stack);
    process.exit(1);
});
