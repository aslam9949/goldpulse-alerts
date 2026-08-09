module.exports = {
  apps: [{
    name: "goldpulse",
    script: "main.py",
    interpreter: "/root/goldpulse-alerts/venv/bin/python3",
    max_memory_restart: "512M",
    autorestart: true,
    max_restarts: 10,
    min_uptime: "10s",
    restart_delay: 5000,
    error_file: "logs/pm2-error.log",
    out_file: "logs/pm2-out.log",
    log_date_format: "YYYY-MM-DD HH:mm:ss Z"
  }]
};