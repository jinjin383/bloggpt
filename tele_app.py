import os
from fastapi import FastAPI, HTTPException, Depends, Body, UploadFile, File
from fastapi.staticfiles import StaticFiles
from telethon import TelegramClient, functions, types, events
from telethon.sessions import StringSession
from telethon.errors import SessionPasswordNeededError, PhoneCodeInvalidError
from telethon.tl.types import InputPeerUser, InputPeerChannel
from telethon.tl.functions.channels import InviteToChannelRequest
from pydantic import BaseModel
from contextlib import asynccontextmanager
import shutil
import base64
from uuid import uuid4


app = FastAPI()

class APICredentials(BaseModel):
    app_id: int
    app_hash: str

class BotToken(BaseModel):
    token: str

class MessageRequest(BaseModel):
    session_hash: str
    recipient: str  # This can be a username, user ID, or channel username
    message: str

class JoinChannelRequest(BaseModel):
    session_hash: str
    channel: str

class SessionHash(BaseModel):
    hash: str

class PhoneNumber(BaseModel):
    phone: str

class OTPVerification(BaseModel):
    phone: str
    code: str
    phone_code_hash: str

class StoryRequest(BaseModel):
    file_path: str
    spoiler: bool = True
    ttl_seconds: int = 42

class TwoFAPassword(BaseModel):
    password: str
    session_hash: str

class TelegramClientManager:
    def __init__(self):
        self.clients = {}
        self.app_id = None
        self.app_hash = None

    async def add_message_handler(self, client):
        @client.on(events.NewMessage(pattern='/ping'))
        async def ping_handler(event):
            await event.reply('pong')

    async def get_client(self, session_hash: str):
        if session_hash not in self.clients:
            raise HTTPException(status_code=400, detail="Session not found")
        return self.clients[session_hash]

    async def create_client(self, session_hash: str = None):
        if not self.app_id or not self.app_hash:
            raise ValueError("API credentials not set")
        
        if session_hash and session_hash in self.clients:
            return session_hash

        session = StringSession(session_hash) if session_hash else StringSession()
        client = TelegramClient(session, self.app_id, self.app_hash)
        await client.connect()

        new_hash = session.save()
        self.clients[new_hash] = client
        await self.add_message_handler(client)
        return new_hash

    async def create_bot_client(self, bot_token: str):
        if not self.app_id or not self.app_hash:
            raise ValueError("API credentials not set")
        
        session = StringSession()
        client = TelegramClient(session, self.app_id, self.app_hash)
        await client.start(bot_token=bot_token)

        new_hash = session.save()
        self.clients[new_hash] = client
        await self.add_message_handler(client)
        return new_hash

    async def remove_client(self, session_hash: str):
        if session_hash in self.clients:
            await self.clients[session_hash].disconnect()
            del self.clients[session_hash]

    async def disconnect_all(self):
        for client in self.clients.values():
            await client.disconnect()
        self.clients.clear()

client_manager = TelegramClientManager()

@asynccontextmanager
async def lifespan(app: FastAPI):
    yield
    await client_manager.disconnect_all()

app = FastAPI(lifespan=lifespan)

async def get_client():
    return await client_manager.get_client()

# Create an uploads directory if it doesn't exist
UPLOAD_DIR = "uploads"
os.makedirs(UPLOAD_DIR, exist_ok=True)

# Mount the uploads directory
app.mount("/uploads", StaticFiles(directory="uploads"), name="uploads")

class Base64Image(BaseModel):
    filename: str
    base64_data: str

@app.post("/set_api_credentials")
async def set_api_credentials(credentials: APICredentials):
    client_manager.app_id = credentials.app_id
    client_manager.app_hash = credentials.app_hash
    return {"message": "API credentials set successfully"}

@app.post("/upload_base64_image")
async def upload_base64_image(image: Base64Image):
    try:
        # Decode the base64 data
        image_data = base64.b64decode(image.base64_data)
        
        # Generate a unique filename
        file_extension = os.path.splitext(image.filename)[1]
        unique_filename = f"{uuid4()}{file_extension}"
        file_path = os.path.join(UPLOAD_DIR, unique_filename)
        
        # Save the file
        with open(file_path, "wb") as file:
            file.write(image_data)
        
        # Generate the URI
        file_uri = f"uploads/{unique_filename}"
        
        return {"file_uri": file_uri, "message": "Image uploaded successfully"}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/upload_image")
async def upload_image(file: UploadFile = File(...)):
    try:
        # Generate a unique filename
        file_extension = os.path.splitext(file.filename)[1]
        unique_filename = f"{uuid4()}{file_extension}"
        file_path = os.path.join(UPLOAD_DIR, unique_filename)
        
        # Save the file
        with open(file_path, "wb") as buffer:
            shutil.copyfileobj(file.file, buffer)
        
        # Generate the URI
        file_uri = f"uploads/{unique_filename}"
        
        return {"file_uri": file_uri, "message": "Image uploaded successfully"}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/verify_2fa")
async def verify_2fa(two_fa: TwoFAPassword):
    try:
        client = await client_manager.get_client(two_fa.session_hash)
        await client.sign_in(password=two_fa.password)
        user = await client.get_me()
        return {"message": f"Authenticated as {user.first_name}", "session_hash": two_fa.session_hash}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/create_session")
async def create_session(phone_number: PhoneNumber):
    if not client_manager.app_id or not client_manager.app_hash:
        raise HTTPException(status_code=400, detail="API credentials not set. Please call /set_api_credentials first.")
    
    try:
        session_hash = await client_manager.create_client()
        client = await client_manager.get_client(session_hash)
        result = await client.send_code_request(phone_number.phone, force_sms=True)
        return {"session_hash": session_hash, "phone_code_hash": result.phone_code_hash}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/create_bot_session")
async def create_bot_session(bot_token: BotToken):
    try:
        session_hash = await client_manager.create_bot_client(bot_token.token)
        client = await client_manager.get_client(session_hash)
        bot_info = await client.get_me()
        return {
            "session_hash": session_hash,
            "bot_info": {
                "id": bot_info.id,
                "first_name": bot_info.first_name,
                "username": bot_info.username,
                "bot": bot_info.bot
            }
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/verify_otp")
async def verify_otp(
    otp_verification: OTPVerification = Body(...),
    session: SessionHash = Body(...)
):
    try:
        client = await client_manager.get_client(session.hash)
        await client.sign_in(
            otp_verification.phone,
            code=otp_verification.code,
            phone_code_hash=otp_verification.phone_code_hash,
        )
        user = await client.get_me()
        return {"message": f"Authenticated as {user.first_name}", "session_hash": session.hash}
    except SessionPasswordNeededError:
        return {"message": "Two-step verification is enabled. Please provide the password.", "session_hash": session.hash}
    except PhoneCodeInvalidError:
        raise HTTPException(status_code=401, detail="Invalid code provided.")
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/send_story")
async def send_story(story_request: StoryRequest, session: SessionHash):
    try:
        client = await client_manager.get_client(session.hash)
        if not await client.is_user_authorized():
            raise HTTPException(status_code=401, detail="Unauthorized. Please authenticate first.")
        me = await client.get_me()    
        result = await client(functions.stories.SendStoryRequest(
            peer=me.id,
            media=types.InputMediaUploadedPhoto(
                file=await client.upload_file(story_request.file_path),
                spoiler=story_request.spoiler,
                ttl_seconds=story_request.ttl_seconds
            ),
            privacy_rules=[types.InputPrivacyValueAllowContacts()]
        ))
        return {"message": "Story sent successfully"}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/send_message")
async def send_message(message_request: MessageRequest):
    try:
        client = await client_manager.get_client(message_request.session_hash)
        
        # Try to interpret the recipient as an integer (user ID) first
        try:
            recipient_id = int(message_request.recipient)
            entity = InputPeerUser(recipient_id, 0)
        except ValueError:
            # If it's not an integer, treat it as a username or channel
            entity = await client.get_entity(message_request.recipient)
        
        # Send the message
        result = await client.send_message(entity, message_request.message)
        
        return {"message": "Message sent successfully", "message_id": result.id}
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/join_channel")
async def join_channel(request: JoinChannelRequest):
    try:
        client = await client_manager.get_client(request.session_hash)
        
        # Get the channel entity
        channel = await client.get_entity(request.channel)
        
        # Join the channel
        await client(InviteToChannelRequest(channel, [client.get_me()]))
        
        return {"message": f"Successfully joined channel {request.channel}"}
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
