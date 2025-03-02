import asyncio
import websockets
import json
import redis
import bcrypt
from datetime import datetime
from cryptography.fernet import Fernet

ENCRYPTION_KEY = Fernet.generate_key()
cipher_suite = Fernet(ENCRYPTION_KEY)

# Redis setup
r = redis.Redis(host='127.0.0.1', port=6379, db=0, decode_responses=True)
pubsub = r.pubsub()

def encrypt_message(message: str) -> str:
        """Encrypt a message using AES."""
        return cipher_suite.encrypt(message.encode('utf-8')).decode('utf-8')

def decrypt_message(encrypted_message: str) -> str:
    """Decrypt a message using AES."""
    return cipher_suite.decrypt(encrypted_message.encode('utf-8')).decode('utf-8')

class ChatServer:
    def __init__(self):
        self.connections = {}  # Active WebSocket connections
        self.online_users = set()  # Track online users
    
    async def handle_auth(self, data):
        """Handle authentication logic WITHOUT sending messages"""
        try:
            if data['type'] == 'register':
                if r.hexists("users", data['username']):
                    return {'success': False, 'message': 'Username taken'}
                
                hashed = bcrypt.hashpw(data['password'].encode(), bcrypt.gensalt())
                r.hset("users", data['username'], hashed.decode('utf-8'))
                return {'success': True, 'message': 'Registration successful'}
                
            elif data['type'] == 'login':
                hashed_password = r.hget("users", data['username'])
                if not hashed_password:
                    return {'success': False, 'message': 'Invalid credentials'}
                
                if not bcrypt.checkpw(data['password'].encode(), hashed_password.encode()):
                    return {'success': False, 'message': 'Invalid credentials'}
                
                return {'success': True, 'message': 'Login successful'}

        except Exception as e:
            return {'success': False, 'message': str(e)}
    
    async def handle_connection(self, websocket):
        username = None
        try:
            # Allow multiple auth attempts
            while True:
                auth_data = json.loads(await websocket.recv())
                auth_result = await self.handle_auth(auth_data)
                
                # Send ONLY the auth result first
                await websocket.send(json.dumps(auth_result))
    
                if auth_result.get('success'):
                    username = auth_data['username']
                    self.connections[username] = websocket
                    self.online_users.add(username)
                    
                    # Send contacts and messages AFTER auth confirmation
                    contacts = r.smembers(f"contacts:{username}")
                    await websocket.send(json.dumps({
                        'type': 'contact_list',
                        'contacts': list(contacts)
                    }))
                    
                    # Decrypt and send pending messages
                    pending_messages = r.lrange(f"messages:{username}", 0, -1)
                    for msg in pending_messages:
                        msg_data = json.loads(msg)
                        msg_data['message'] = decrypt_message(msg_data['message'])  # Decrypt message
                        await websocket.send(json.dumps(msg_data))
                    r.delete(f"messages:{username}")
                    
                    await self.notify_contacts(username, True)
                    break  # Exit auth loop
    
            # Handle messages after successful auth
            async for message in websocket:
                data = json.loads(message)
                if data['type'] == 'add_contact':
                    await self.add_contact(username, data['contact'])
                elif data['type'] == 'message':
                    await self.route_message(username, data)
    
        except websockets.ConnectionClosed:
            print(f"{username} disconnected")
        finally:
            if username:
                if username in self.connections:
                    del self.connections[username]
                if username in self.online_users:
                    self.online_users.remove(username)
                    await self.notify_contacts(username, False)
    async def add_contact(self, username, contact):
        """Add a contact with mutual check."""
        try:
            if not r.hexists("users", contact):
                await self.connections[username].send(json.dumps({
                    'type': 'error',
                    'message': f'User {contact} does not exist'
                }))
                return

            if r.sismember(f"contacts:{username}", contact):
                await self.connections[username].send(json.dumps({
                    'type': 'error',
                    'message': f'{contact} is already in your contacts'
                }))
                return

            r.sadd(f"contacts:{username}", contact)
            await self.connections[username].send(json.dumps({
                'type': 'system',
                'message': f'{contact} added to your contacts'
            }))

            # Notify both users if mutual contact
            if r.sismember(f"contacts:{contact}", username):
                await self.connections[username].send(json.dumps({
                    'type': 'system',
                    'message': f'You are now mutual contacts with {contact}'
                }))
                if contact in self.connections:
                    await self.connections[contact].send(json.dumps({
                        'type': 'system',
                        'message': f'You are now mutual contacts with {username}'
                    }))

        except Exception as e:
            await self.connections[username].send(json.dumps({
                'type': 'error',
                'message': str(e)
            }))

    async def route_message(self, sender, data):
        """Route messages with mutual contact verification."""
        try:
            receiver = data['to']
            
            # Check receiver existence
            if not r.hexists("users", receiver):
                await self.connections[sender].send(json.dumps({
                    'type': 'error',
                    'message': f"User {receiver} does not exist"
                }))
                return
    
            # Check mutual contacts
            if not (r.sismember(f"contacts:{sender}", receiver) and r.sismember(f"contacts:{receiver}", sender)):
                await self.connections[sender].send(json.dumps({
                    'type': 'error',
                    'message': f"You can only message mutual contacts. {receiver} needs to add you back."
                }))
                return
    
            # Encrypt the message
            encrypted_message = encrypt_message(data['message'])
    
            # Store encrypted message with timestamp
            timestamp = datetime.now().isoformat()
            message_data = {
                'type': 'message',
                'from': sender,
                'message': encrypted_message,  # Store encrypted message
                'timestamp': timestamp
            }
    
            if receiver in self.connections:
                # Send decrypted message to online user
                await self.connections[receiver].send(json.dumps({
                    'type': 'message',
                    'from': sender,
                    'message': data['message'],  # Send plain text
                    'timestamp': timestamp
                }))
            else:
                # Store encrypted message for offline user
                r.rpush(f"messages:{receiver}", json.dumps(message_data))
                await self.connections[sender].send(json.dumps({
                    'type': 'system',
                    'message': f"{receiver} is offline. Message will be delivered when they're back."
                }))
    
        except Exception as e:
            print(f"Message routing error: {str(e)}")
            await self.connections[sender].send(json.dumps({
                'type': 'error',
                'message': 'Failed to send message'
            }))
    
    async def notify_contacts(self, username, is_online):
        """Notify contacts about a user's status change."""
        contacts = r.smembers(f"contacts:{username}")
        for contact in contacts:
            if contact in self.connections:
                await self.connections[contact].send(json.dumps({
                    'type': 'status',
                    'user': username,
                    'status': 'online' if is_online else 'offline'
                }))

async def main():
    server = ChatServer()
    print("Starting server on ws://localhost:8765")
    async with websockets.serve(server.handle_connection, "localhost", 8765) as server:
        print(f"Server successfully started! Listening on {server.sockets[0].getsockname()}")
        await asyncio.Future()

asyncio.run(main())