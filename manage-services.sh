#!/bin/bash
# Service Management Script

case "$1" in
  start)
    echo "🚀 Starting all services..."
    sudo systemctl start mcp-server
    sleep 2
    sudo systemctl start agent-api
    sleep 2
    sudo systemctl start slack-bot
    echo "✅ All services started"
    ;;
    
  stop)
    echo "🛑 Stopping all services..."
    sudo systemctl stop slack-bot
    sudo systemctl stop agent-api
    sudo systemctl stop mcp-server
    echo "✅ All services stopped"
    ;;
    
  restart)
    echo "🔄 Restarting all services..."
    sudo systemctl restart mcp-server
    sleep 2
    sudo systemctl restart agent-api
    sleep 2
    sudo systemctl restart slack-bot
    echo "✅ All services restarted"
    ;;
    
  status)
    echo "📊 Service Status:"
    echo "==================="
    echo "🔧 MCP Server (Port 3001):"
    sudo systemctl status mcp-server --no-pager -l
    echo ""
    echo "🤖 Agent API (Port 8000):"
    sudo systemctl status agent-api --no-pager -l
    echo ""
    echo "💬 Slack Bot (Port 3002):"
    sudo systemctl status slack-bot --no-pager -l
    ;;
    
  logs)
    echo "📋 Recent logs from all services:"
    echo "=================================="
    echo "🔧 MCP Server logs:"
    sudo journalctl -u mcp-server -n 10 --no-pager
    echo ""
    echo "🤖 Agent API logs:"
    sudo journalctl -u agent-api -n 10 --no-pager
    echo ""
    echo "💬 Slack Bot logs:"
    sudo journalctl -u slack-bot -n 10 --no-pager
    ;;
    
  follow)
    echo "📋 Following logs from all services (Ctrl+C to exit):"
    sudo journalctl -u mcp-server -u agent-api -u slack-bot -f
    ;;
    
  test)
    echo "🧪 Testing all services..."
    echo "Testing MCP Server (3001):"
    curl -s http://localhost:3001/health || echo "❌ MCP Server not responding"
    echo ""
    echo "Testing Agent API (8000):"
    curl -s http://localhost:8000/health || echo "❌ Agent API not responding"
    echo ""
    echo "Testing Slack Bot (3002):"
    curl -s http://localhost:3002/health || echo "❌ Slack Bot not responding"
    ;;
    
  *)
    echo "Usage: $0 {start|stop|restart|status|logs|follow|test}"
    echo ""
    echo "Commands:"
    echo "  start   - Start all services"
    echo "  stop    - Stop all services"
    echo "  restart - Restart all services"
    echo "  status  - Show service status"
    echo "  logs    - Show recent logs"
    echo "  follow  - Follow live logs"
    echo "  test    - Test all endpoints"
    exit 1
    ;;
esac