#!/usr/bin/env node
import express, { Request, Response } from "express";
import { randomUUID } from "node:crypto";
import { McpServer } from "@modelcontextprotocol/sdk/server/mcp.js";
import { StreamableHTTPServerTransport } from "@modelcontextprotocol/sdk/server/streamableHttp.js";
import { z } from "zod";
import { PrismaClient, Prisma } from '@prisma/client'; // Import Prisma for types
import dotenv from 'dotenv';

// Load environment variables
dotenv.config();

// Initialize Prisma Client
const prisma = new PrismaClient();

// Utility function to check if request is an initialize request
function isInitializeRequest(body: any): boolean {
  return body && body.method === 'initialize';
}

class MeetingRoomServer {
  private app: express.Application;
  private transports: { [sessionId: string]: StreamableHTTPServerTransport } = {};
  private port: number;

  constructor(port: number = 3000) {
    this.port = port;
    this.app = express();
    this.app.use(express.json());
    this.setupRoutes();
  }

  private createServer(): McpServer {
    const server = new McpServer({
      name: "meeting-room-server-prisma",
      version: "0.5.0"
    });
    this.setupTools(server);
    return server;
  }

  private setupTools(server: McpServer): void {
    // --- All tools are now refactored to use Prisma Client ---

    // Get available rooms tool
    server.tool("get_available_rooms", {
      date: z.string().describe("Date in YYYY-MM-DD format"),
      start_time: z.string().describe("Start time in HH:MM format (24-hour)"),
      end_time: z.string().describe("End time in HH:MM format (24-hour)")
    }, async ({ date, start_time, end_time }) => {
      const startDateTime = new Date(`${date}T${start_time}:00`);
      const endDateTime = new Date(`${date}T${end_time}:00`);

      try {
        // Find rooms that do NOT have any confirmed bookings that overlap with the requested time
        const availableRooms = await prisma.room.findMany({
          where: {
            NOT: {
              bookings: {
                some: {
                  status: 'confirmed',
                  start_time: { lt: endDateTime },
                  end_time: { gt: startDateTime },
                },
              },
            },
          },
          orderBy: { name: 'asc' },
        });

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
      } catch (error) {
        throw new Error(`Failed to get available rooms: ${error instanceof Error ? error.message : String(error)}`);
      }
    });

    // Book room tool
    server.tool("book_room", {
      room_id: z.number().describe("ID of the room to book"),
      date: z.string().describe("Date in YYYY-MM-DD format"),
      start_time: z.string().describe("Start time in HH:MM format (24-hour)"),
      end_time: z.string().describe("End time in HH:MM format (24-hour)"),
      name: z.string().describe("Name of the person booking the room")
    }, async ({ room_id, date, start_time, end_time, name }) => {
      const startDateTime = new Date(`${date}T${start_time}:00`);
      const endDateTime = new Date(`${date}T${end_time}:00`);

      try {
        // Use a transaction to check for conflicts and create the booking atomically
        const result = await prisma.$transaction(async (tx: Prisma.TransactionClient) => {
          const room = await tx.room.findUnique({ where: { id: room_id } });
          if (!room) {
            throw new Error(`Room with ID ${room_id} not found`);
          }

          const conflictingBooking = await tx.booking.findFirst({
            where: {
              room_id: room_id,
              status: 'confirmed',
              start_time: { lt: endDateTime },
              end_time: { gt: startDateTime },
            },
          });

          if (conflictingBooking) {
            throw new Error(`Room '${room.name}' is already booked during the requested time`);
          }

          const newBooking = await tx.booking.create({
            data: {
              room_id: room_id,
              start_time: startDateTime,
              end_time: endDateTime,
              status: 'confirmed',
              name: name.trim(),
            },
          });

          return { booking: newBooking, room };
        });

        return {
          content: [{
            type: "text",
            text: JSON.stringify({
              success: true,
              booking_id: result.booking.id,
              room_name: result.room.name,
              booked_by: name.trim(),
              booking_details: { date, start_time, end_time, status: 'confirmed' },
              message: `Room '${result.room.name}' successfully booked by ${name.trim()} for ${date} from ${start_time} to ${end_time}`
            }, null, 2)
          }]
        };
      } catch (error) {
        throw new Error(`Failed to book room: ${error instanceof Error ? error.message : String(error)}`);
      }
    });

    // Get room bookings tool
    server.tool("get_room_bookings", {
      room_id: z.number().describe("ID of the room"),
      date: z.string().describe("Date in YYYY-MM-DD format")
    }, async ({ room_id, date }) => {
      const startOfDay = new Date(`${date}T00:00:00.000Z`);
      const endOfDay = new Date(`${date}T23:59:59.999Z`);

      try {
        const roomWithBookings = await prisma.room.findUnique({
          where: { id: room_id },
          include: {
            bookings: {
              where: {
                status: 'confirmed',
                start_time: { gte: startOfDay, lte: endOfDay },
              },
              orderBy: { start_time: 'asc' },
            },
          },
        });

        if (!roomWithBookings) {
          throw new Error(`Room with ID ${room_id} not found.`);
        }

        return {
          content: [{
            type: "text",
            text: JSON.stringify({
              room_id,
              room_name: roomWithBookings.name,
              date,
              bookings: roomWithBookings.bookings,
              total_bookings: roomWithBookings.bookings.length
            }, null, 2)
          }]
        };
      } catch (error) {
        throw new Error(`Failed to get room bookings: ${error instanceof Error ? error.message : String(error)}`);
      }
    });

    // Cancel booking tool
    server.tool("cancel_booking", {
      booking_id: z.number().describe("ID of the booking to cancel")
    }, async ({ booking_id }) => {
      try {
        const booking = await prisma.booking.findUnique({
            where: { id: booking_id },
            include: { room: true }
        });

        if (!booking) {
            throw new Error(`Booking with ID ${booking_id} not found`);
        }

        await prisma.booking.update({
            where: { id: booking_id },
            data: { status: 'cancelled' },
        });

        return {
          content: [{
            type: "text",
            text: JSON.stringify({
              success: true,
              message: "Booking cancelled successfully",
              cancelled_booking: {
                  booking_id: booking.id,
                  room_name: booking.room.name,
                  booked_by: booking.name,
                  start_time: booking.start_time,
                  end_time: booking.end_time,
                }
            }, null, 2)
          }]
        };
      } catch (error) {
        throw new Error(`Failed to cancel booking: ${error instanceof Error ? error.message : String(error)}`);
      }
    });

    // Get all rooms tool
    server.tool("get_all_rooms", {}, async () => {
      try {
        const rooms = await prisma.room.findMany({
            orderBy: { name: 'asc' }
        });

        return {
          content: [{
            type: "text",
            text: JSON.stringify({
              rooms: rooms,
              total_rooms: rooms.length
            }, null, 2)
          }]
        };
      } catch (error) {
        throw new Error(`Failed to get rooms: ${error instanceof Error ? error.message : String(error)}`);
      }
    });
  }

  private setupRoutes(): void {
    this.app.post('/mcp', async (req: Request, res: Response) => {
      // This routing logic remains the same as before
      console.log('Received MCP request:', req.body);
      try {
        const sessionId = req.headers['mcp-session-id'] as string | undefined;
        let transport: StreamableHTTPServerTransport;
        if (sessionId && this.transports[sessionId]) {
          transport = this.transports[sessionId];
        } else if (!sessionId && isInitializeRequest(req.body)) {
          transport = new StreamableHTTPServerTransport({
            sessionIdGenerator: () => randomUUID(),
            onsessioninitialized: (sessionId) => {
              this.transports[sessionId] = transport;
              console.log(`New MCP session initialized: ${sessionId}`);
            }
          });
          transport.onclose = () => {
            if (transport.sessionId) {
              delete this.transports[transport.sessionId];
              console.log(`MCP session closed: ${transport.sessionId}`);
            }
          };
          const server = this.createServer();
          await server.connect(transport);
        } else {
          res.status(400).json({ jsonrpc: '2.0', error: { code: -32000, message: 'Bad Request: No valid session ID provided' }, id: null });
          return;
        }
        await transport.handleRequest(req, res, req.body);
      } catch (error) {
        console.error('Error handling MCP request:', error);
        if (!res.headersSent) {
          res.status(500).json({ jsonrpc: '2.0', error: { code: -32603, message: 'Internal server error' }, id: null });
        }
      }
    });

    const handleSessionRequest = async (req: Request, res: Response) => {
      const sessionId = req.headers['mcp-session-id'] as string | undefined;
      if (!sessionId || !this.transports[sessionId]) {
        res.status(400).send('Invalid or missing session ID');
        return;
      }
      const transport = this.transports[sessionId];
      await transport.handleRequest(req, res);
    };

    this.app.get('/mcp', handleSessionRequest);
    this.app.delete('/mcp', handleSessionRequest);

    this.app.get('/health', (req: Request, res: Response) => {
      res.json({ status: 'healthy', server: 'meeting-room-mcp-server-prisma', version: '0.5.0', active_sessions: Object.keys(this.transports).length });
    });

    this.app.get('/', (req: Request, res: Response) => {
      res.json({ name: 'Meeting Room MCP Server (Prisma)', version: '0.5.0', mcp_endpoint: '/mcp' });
    });
  }

  async run() {
    try {
      // Test Prisma connection
      await prisma.$connect();
      console.log("✅ Successfully connected to the database via Prisma.");
    } catch (err) {
      console.error("❌ Failed to connect to the database via Prisma:", err);
      process.exit(1);
    }

    const server = this.app.listen(this.port, () => {
      console.log(`🚀 Meeting Room MCP Server (Prisma) running on http://localhost:${this.port}`);
    });

    // Graceful shutdown
    const shutdown = async (signal: string) => {
        console.log(`\n🛑 Received ${signal}. Shutting down gracefully...`);
        await prisma.$disconnect();
        server.close(() => {
            console.log('✅ Server closed.');
            process.exit(0);
        });
    };

    process.on('SIGINT', () => shutdown('SIGINT'));
    process.on('SIGTERM', () => shutdown('SIGTERM'));
  }
}

const port = process.env.PORT ? parseInt(process.env.PORT) : 3001;
const server = new MeetingRoomServer(port);

server.run().catch((error) => {
  console.error('❌ Fatal error starting MCP server:', error);
  process.exit(1);
});
