import asyncio
import websockets
import json
import logging
from datetime import datetime
from typing import Optional, List
from websockets.exceptions import ConnectionClosed
from prompt_toolkit import PromptSession
from prompt_toolkit.patch_stdout import patch_stdout
from prompt_toolkit.styles import Style
from plyer import notification
import os
import platform
import warnings
from rich.console import Console
from rich.panel import Panel
from rich.text import Text
from rich.table import Table

# Initialize rich console with force_terminal=False
console = Console(force_terminal=False)

# Suppress plyer warnings about missing dbus
warnings.filterwarnings("ignore", category=UserWarning, module="plyer")

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Define prompt_toolkit style
prompt_style = Style.from_dict({
    "prompt": "cyan",
    "bottom_toolbar": "green",
})

class ChatClient:
    def __init__(self):
        self.server_url = "wss://chat.giwatech.site"
        self.websocket: Optional[websockets.WebSocketClientProtocol] = None
        self.running = True
        self.prompt_session = PromptSession()
        self.username: Optional[str] = None
        self.contacts: List[str] = []
        self.online_status = {}  # Track online status of contacts

    async def connect(self):
        """Connect to the WebSocket server."""
        retry_delay = 1
        while self.running:
            try:
                console.print("[yellow]Connecting to server at {}...[/yellow]".format(self.server_url))  # Debug logging
                self.websocket = await websockets.connect(
                    self.server_url, ping_interval=20, ping_timeout=60
                )
                console.print("[green]Connection successful![/green]")  # Debug logging
                return True
            except Exception as e:
                logger.error(f"Connection error: {str(e)}")
                await asyncio.sleep(retry_delay)
                retry_delay = min(retry_delay * 2, 30)
        return False

    async def authenticate(self):
        """Handle user authentication (register/login)."""
        console.print(Panel.fit("[magenta]=== BlackChat ===", style="bold magenta"))
        print_ascii_art()  # Display ASCII art
        while True:
            try:
                # Use prompt_toolkit for input with custom style
                choice = await self.prompt_session.prompt_async(
                    "1. Register\n2. Login\n> ",
                    style=prompt_style,
                    bottom_toolbar="Ctrl+C to exit",
                )
                if choice not in ("1", "2"):
                    continue

                username = await self.prompt_session.prompt_async(
                    "Username: ",
                    style=prompt_style,
                )
                password = await self.prompt_session.prompt_async(
                    "Password: ",
                    style=prompt_style,
                    is_password=True,  # Mask password input
                )

                # Reset is_password for subsequent inputs
                self.prompt_session = PromptSession()

                await self.websocket.send(json.dumps({
                    "type": "register" if choice == "1" else "login",
                    "username": username,
                    "password": password
                }))

                response = json.loads(await self.websocket.recv())
                if response.get("success"):
                    self.username = username
                    console.print(f"\n[green][System] {response['message']}[/green]")
                    return True
                console.print(f"\n[red][Error] {response['message']}[/red]")

            except (asyncio.CancelledError, KeyboardInterrupt):
                raise
            except Exception as e:
                logger.error(f"Auth error: {str(e)}")
                return False

    async def handle_input(self):
        """Handle user input and send commands to the server."""
        with patch_stdout():
            while self.running:
                try:
                    # Use prompt_toolkit for input with custom style
                    cmd = await self.prompt_session.prompt_async(
                        "> ",
                        style=prompt_style,
                        bottom_toolbar=f"User: {self.username}",
                    )
                    if cmd == "/quit":
                        await self.websocket.close()
                        self.running = False
                        break
                    elif cmd == "/clear":
                        self.clear_screen()
                    elif cmd.startswith("/add "):
                        contact = cmd.split(maxsplit=1)[1]
                        await self.websocket.send(json.dumps({
                            "type": "add_contact",
                            "contact": contact
                        }))
                    elif cmd.startswith("/msg "):
                        parts = cmd.split(maxsplit=2)
                        if len(parts) < 3:
                            console.print("\n[red][Error] Usage: /msg <user> <message>[/red]")
                            continue
                        _, recipient, message = parts
                        await self.websocket.send(json.dumps({
                            "type": "message",
                            "to": recipient,
                            "message": message
                        }))
                    elif cmd == "/contacts":
                        if console.is_terminal:
                            table = Table(title="Contacts", show_header=True, header_style="bold magenta")
                            table.add_column("Username", style="cyan")
                            table.add_column("Status", style="green")
                            for contact in self.contacts:
                                status = "Online" if self.online_status.get(contact, False) else "Offline"
                                table.add_row(contact, status)
                            console.print(table)
                        else:
                            console.print("\nContacts:")
                            for contact in self.contacts:
                                status = "Online" if self.online_status.get(contact, False) else "Offline"
                                console.print(f"{contact}: {status}")
                    elif cmd == "/help":
                        console.print("\n[yellow]Commands:[/yellow]")
                        console.print("[cyan]/add <username> - Add contact[/cyan]")
                        console.print("[cyan]/msg <user> <message> - Send message[/cyan]")
                        console.print("[cyan]/contacts - List contacts[/cyan]")
                        console.print("[cyan]/clear - Clear the screen[/cyan]")
                        console.print("[cyan]/quit - Exit[/cyan]")
                    else:
                        console.print("\n[red][Error] Unknown command. Use /help[/red]")

                except (KeyboardInterrupt, asyncio.CancelledError):
                    self.running = False
                    break

    async def show_notification(self, title: str, message: str, timeout: int = 10):
        """Show a notification with a custom timeout."""
        try:
            notification.notify(
                title=title,
                message=message[:256],  # Limit message length
                app_name="BlackChat",
                timeout=timeout  # Notification timeout in seconds
            )
            if platform.system() == "Linux":
                os.system('paplay /usr/share/sounds/freedesktop/stereo/message.oga')
        except Exception as e:
            logger.error(f"Notification failed: {str(e)}")

    async def handle_messages(self):
        """Handle incoming messages from the server."""
        try:
            async for message in self.websocket:
                data = json.loads(message)
                if data["type"] == "contact_list":
                    self.contacts = data["contacts"]
                elif data["type"] == "message":
                    dt = datetime.fromisoformat(data['timestamp'])
                    timestamp = dt.strftime("%H:%M:%S")
                    if console.is_terminal:
                        message_text = Text.assemble(
                            ("[", "dim"),
                            (timestamp, "blue"),
                            ("] ", "dim"),
                            (data['from'], "green"),
                            (": ", "dim"),
                            (data['message'], "white")
                        )
                        console.print(message_text)
                    else:
                        console.print(f"[{timestamp}] {data['from']}: {data['message']}")
                    
                    # Show desktop notification with custom timeout
                    asyncio.create_task(self.show_notification(
                        title=f"New message from {data['from']}",
                        message=data['message'],
                        timeout=10  # 10 seconds
                    ))
                elif data["type"] == "status":
                    self.online_status[data['user']] = (data['status'] == 'online')
                    console.print(f"\n[yellow][System] {data['user']} is now {data['status']}[/yellow]")
                elif data["type"] in ("system", "error"):
                    console.print(f"\n[red][{data['type'].title()}] {data['message']}[/red]")

        except ConnectionClosed:
            console.print("\n[red][System] Connection lost. Reconnecting...[/red]")
            await self.reconnect()

    async def reconnect(self):
        """Reconnect to the server."""
        await self.websocket.close()
        if await self.connect() and await self.authenticate():
            await self.handle_messages()

    def clear_screen(self):
        """Clear the terminal screen."""
        if platform.system() == "Windows":
            os.system("cls")
        else:
            os.system("clear")

    async def run(self):
        """Run the chat client."""
        try:
            if not await self.connect() or not await self.authenticate():
                return

            await asyncio.gather(
                self.handle_messages(),
                self.handle_input()
            )
        except KeyboardInterrupt:
            console.print("\n[red][System] Disconnecting...[/red]")
        finally:
            self.running = False
            if self.websocket:
                await self.websocket.close()

def print_ascii_art():
    """Display ASCII art for BlackChat."""
    
   
    art ="""
    
██████╗  ██████╗
██╔══██╗██╔════╝
██████╔╝██║     
██╔══██╗██║     
██████╔╝╚██████╗
╚═════╝  ╚═════╝
                                                                         
"""

    console.print(f"[bold cyan]{art}[/bold cyan]")
 

if __name__ == "__main__":
    client = ChatClient()
    asyncio.run(client.run())