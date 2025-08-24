#!/bin/bash
# Complete setup for 3 services on one EC2 instance

echo "🚀 Setting up 3-service architecture..."

# ===== SERVICE 1: MCP Server (Port 3001) =====
cat > /etc/systemd/system/mcp-server.service << 'EOF'
[Unit]
Description=MCP Server
After=network.target

[Service]
Type=exec
User=ec2-user
Group=ec2-user
WorkingDirectory=/home/ec2-user/slack-rooms
Environment=PATH=/home/ec2-user/slack-rooms/venv310/bin
Environment=PYTHONPATH=/home/ec2-user/slack-rooms
ExecStart=/home/ec2-user/slack-rooms/venv310/bin/uvicorn mcp_server:app --host 0.0.0.0 --port 3001 --workers 2
Restart=always
RestartSec=3
StandardOutput=journal
StandardError=journal

[Install]
WantedBy=multi-user.target
EOF

# ===== SERVICE 2: Agent API (Port 8000) =====
cat > /etc/systemd/system/agent-api.service << 'EOF'
[Unit]
Description=Agent API with Gemini
After=network.target mcp-server.service
Wants=mcp-server.service

[Service]
Type=exec
User=ec2-user
Group=ec2-user
WorkingDirectory=/home/ec2-user/slack-rooms
Environment=PATH=/home/ec2-user/slack-rooms/venv310/bin
Environment=PYTHONPATH=/home/ec2-user/slack-rooms
Environment=GEMINI_API_KEY=AIzaSyB_nXNqTCAiZZH6SqYRUsPwxtGa6kDlay8
ExecStart=/home/ec2-user/slack-rooms/venv310/bin/uvicorn agnoagent:app --host 0.0.0.0 --port 8000 --workers 2
Restart=always
RestartSec=3
StandardOutput=journal
StandardError=journal

[Install]
WantedBy=multi-user.target
EOF

# ===== SERVICE 3: Slack Bot (Port 3002) =====
cat > /etc/systemd/system/slack-bot.service << 'EOF'
[Unit]
Description=Slack Bot FastAPI
After=network.target agent-api.service
Wants=agent-api.service

[Service]
Type=exec
User=ec2-user
Group=ec2-user
WorkingDirectory=/home/ec2-user/slack-rooms
Environment=PATH=/home/ec2-user/slack-rooms/venv310/bin
Environment=PYTHONPATH=/home/ec2-user/slack-rooms
ExecStart=/home/ec2-user/slack-rooms/venv310/bin/uvicorn bot:app --host 0.0.0.0 --port 3002 --workers 2
Restart=always
RestartSec=3
StandardOutput=journal
StandardError=journal

[Install]
WantedBy=multi-user.target
EOF

echo "✅ Service files created!"

# Reload systemd
sudo systemctl daemon-reload

# Enable all services (start on boot)
sudo systemctl enable mcp-server
sudo systemctl enable agent-api  
sudo systemctl enable slack-bot

echo "✅ Services enabled for auto-start"

# Start all services
echo "🚀 Starting all services..."
sudo systemctl start mcp-server
sleep 3
sudo systemctl start agent-api
sleep 3
sudo systemctl start slack-bot

echo "✅ All services started!"

# Check status
echo "📊 Service Status:"
sudo systemctl status mcp-server --no-pager -l
sudo systemctl status agent-api --no-pager -l
sudo systemctl status slack-bot --no-pager -l