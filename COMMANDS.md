# GoldPulse Bot — Quick Command Reference

## 🤖 Telegram Bot Commands

| Command | Description |
|---------|-------------|
| `/menu` | Open the menu (easy access to all features) |
| `/start` | Start the bot and show welcome message |
| `/help` | Show all available commands |
| `/price` | Current gold price (USD + MCX) |
| `/latest` | Recent gold news articles |
| `/digest` | Today's morning/evening digest |
| `/upcoming` | Upcoming US macro events |
| `/settings` | Current bot settings |
| `/health` | Bot health check and stats |

**Tip:** Just type `/menu` to access everything from one place!

## 🔄 Service Management

```bash
# Start the bot
systemctl start goldpulse

# Stop the bot
systemctl stop goldpulse

# Restart the bot
systemctl restart goldpulse

# Check if bot is running
systemctl status goldpulse

# Enable auto-start on boot (already done)
systemctl enable goldpulse

# Disable auto-start on boot
systemctl disable goldpulse
```

## 📋 Logs

```bash
# View last 50 log lines
journalctl -u goldpulse -n 50

# Follow live logs (Ctrl+C to exit)
journalctl -u goldpulse -f

# View logs from today
journalctl -u goldpulse --since today

# View only errors
journalctl -u goldpulse -p err

# View logs from last hour
journalctl -u goldpulse --since "1 hour ago"
```

## 🧪 Manual Run (for testing)

```bash
# Activate virtual environment
source /root/goldpulse-alerts/venv/bin/activate

# Run bot manually (foreground)
cd /root/goldpulse-alerts && python3 main.py

# Check Python syntax
python3 -m py_compile main.py
```

## 📁 Important Paths

```
/root/goldpulse-alerts/           # Project root
/root/goldpulse-alerts/.env       # Configuration (tokens, settings)
/root/goldpulse-alerts/data/      # SQLite database
/root/goldpulse-alerts/logs/      # Log files
/root/goldpulse-alerts/venv/      # Python virtual environment
/etc/systemd/system/goldpulse.service  # Service file
```

## ⚙️ Edit Settings

```bash
# Edit bot configuration
nano /root/goldpulse-alerts/.env

# After editing, restart the bot
systemctl restart goldpulse
```

## 🔍 Quick Checks

```bash
# Is bot running?
systemctl is-active goldpulse

# How much memory is it using?
systemctl show goldpulse --property=MemoryCurrent

# Process ID
systemctl show goldpulse --property=MainPID

# Uptime
systemctl show goldpulse --property=ActiveEnterTimestamp
```

## 🛠️ Troubleshooting

```bash
# Bot won't start? Check errors
journalctl -u goldpulse -n 20 --no-pager

# Out of memory? Check limits
systemctl show goldpulse --property=MemoryMax

# Database locked? Restart
systemctl restart goldpulse

# Dependencies broken? Reinstall
source /root/goldpulse-alerts/venv/bin/activate
pip install -r /root/goldpulse-alerts/requirements.txt
```
