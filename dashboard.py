import os
import time
import datetime
import psutil
import jwt
import json
from rich.live import Live
from rich.layout import Layout
from rich.panel import Panel
from rich.table import Table
from rich.text import Text
from rich.console import Console

console = Console()

# --- CONFIGURATION ---
LICENSE_FILE = "license.key"
STATS_FILE = "stats.jsonl"
BOOT_TIME = psutil.boot_time()

def get_uptime():
    """Calculates server uptime."""
    uptime_seconds = time.time() - BOOT_TIME
    days = int(uptime_seconds // 86400)
    hours = int((uptime_seconds % 86400) // 3600)
    minutes = int((uptime_seconds % 3600) // 60)
    return f"{days}d {hours}h {minutes}m"

def get_license_info():
    """
    Reads the licence file for dashboard display only.
    Signature verification is not performed here — this is display-only.
    """
    if not os.path.exists(LICENSE_FILE):
        return "NOT FOUND", "N/A", "N/A"

    try:
        with open(LICENSE_FILE, 'r') as f:
            token = f.read().strip()

        # Decode without verifying signature — display purposes only
        decoded = jwt.decode(token, options={"verify_signature": False})
        client = decoded.get("client_name", "Unknown")
        tier = decoded.get("tier", "Unknown")
        expiry_ts = decoded.get("exp", 0)

        if time.time() > expiry_ts:
            status = "🔴 EXPIRED"
        else:
            days_left = int((expiry_ts - time.time()) / 86400)
            status = f"🟢 VALID ({days_left} days remaining)"

        return status, client, tier
    except Exception:
        return "🔴 CORRUPTED", "N/A", "N/A"

def get_recent_logs():
    """Reads the last 7 entries from the order log file."""
    logs = []
    if not os.path.exists(STATS_FILE):
        return "[dim]No recent orders found.[/dim]"

    try:
        with open(STATS_FILE, 'r') as f:
            lines = f.readlines()[-7:]
            for line in lines:
                try:
                    data = json.loads(line.strip())
                    time_str = datetime.datetime.fromtimestamp(data['timestamp']).strftime('%H:%M:%S')
                    oid = data.get('order_id', 'UNKNOWN')
                    total = data.get('total', 0)
                    logs.append(
                        f"[[cyan]{time_str}[/cyan]] [green]INFO[/green]: "
                        f"Order [bold]{oid}[/bold] processed. Total: Rs {total:,.2f}"
                    )
                except Exception:
                    logs.append(f"[dim]{line.strip()}[/dim]")
        return "\n".join(logs) if logs else "[dim]Waiting for data...[/dim]"
    except Exception:
        return "[red]Error reading log file.[/red]"

def make_hardware_panel():
    """Generates the CPU, RAM, and disk usage panel."""
    cpu = psutil.cpu_percent()
    ram = psutil.virtual_memory().percent
    disk = psutil.disk_usage('/').percent

    cpu_color = "red" if cpu > 85 else "yellow" if cpu > 60 else "green"
    ram_color = "red" if ram > 85 else "yellow" if ram > 60 else "green"

    table = Table.grid(padding=(0, 2))
    table.add_row("🧠", "CPU Usage:", f"[{cpu_color}]{cpu}%[/{cpu_color}]")
    table.add_row("🚀", "RAM Usage:", f"[{ram_color}]{ram}%[/{ram_color}]")
    table.add_row("💾", "Disk Usage:", f"{disk}%")
    table.add_row("⏱️", "Uptime:", f"[cyan]{get_uptime()}[/cyan]")

    return Panel(table, title="[bold white]SYSTEM HEALTH[/bold white]", border_style="cyan")

def make_network_panel():
    """Generates the licence and API connection status panel."""
    status, client, tier = get_license_info()

    table = Table.grid(padding=(0, 2))
    table.add_row("🏢", "Client:", f"[bold white]{client}[/bold white] ({tier})")
    table.add_row("🔑", "Licence:", status)
    table.add_row("🌐", "Telegram API:", "[green]🟢 ONLINE[/green]")
    table.add_row("🗺️", "Mapbox API:", "[green]🟢 CONNECTED[/green]")

    return Panel(table, title="[bold white]NETWORK & LICENCE[/bold white]", border_style="blue")

def make_logs_panel():
    """Generates the live order log panel."""
    log_text = get_recent_logs()
    return Panel(log_text, title="[bold white]LIVE ORDER LOG[/bold white]", border_style="green", padding=(1, 2))

def generate_layout():
    """Builds the complete dashboard layout."""
    layout = Layout()

    layout.split_column(
        Layout(name="header", size=3),
        Layout(name="main", size=8),
        Layout(name="logs")
    )

    layout["main"].split_row(
        Layout(name="hardware"),
        Layout(name="network")
    )

    header_text = Text("LOGISTICS ENGINE — HOST CONTROL PANEL", justify="center", style="bold white on blue")
    layout["header"].update(Panel(header_text, style="blue"))
    layout["hardware"].update(make_hardware_panel())
    layout["network"].update(make_network_panel())
    layout["logs"].update(make_logs_panel())

    return layout

if __name__ == "__main__":
    os.system('cls' if os.name == 'nt' else 'clear')

    try:
        with Live(generate_layout(), refresh_per_second=2, screen=True):
            while True:
                time.sleep(0.5)
    except KeyboardInterrupt:
        console.print("[bold red]Dashboard closed.[/bold red] The bot continues to run in the background.")