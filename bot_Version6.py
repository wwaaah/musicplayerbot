import discord
from discord.ext import commands
from discord import app_commands
import aiosqlite
import yt_dlp
import asyncio
import os

TOKEN = os.getenv("DISCORD_TOKEN")  # Set this in your environment or .env file

# Only enable the intents you actually need!
intents = discord.Intents.default()
intents.message_content = False  # Not needed for slash commands
intents.members = False          # Not needed unless you access the member list
intents.presences = False        # Not needed unless you use presence info
bot = commands.Bot(command_prefix="!", intents=intents)
tree = bot.tree

DB_PATH = "songs.db"

# --- Database helper functions ---

async def db_init():
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("""
            CREATE TABLE IF NOT EXISTS songs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                title TEXT NOT NULL,
                artist TEXT,
                url TEXT NOT NULL
            )
        """)
        await db.commit()

async def find_song_by_query(query):
    async with aiosqlite.connect(DB_PATH) as db:
        q = f"%{query.lower()}%"
        async with db.execute("SELECT title, artist, url FROM songs WHERE LOWER(title) LIKE ? OR LOWER(artist) LIKE ? LIMIT 1", (q, q)) as cursor:
            row = await cursor.fetchone()
            return row

async def find_song_choices(partial):
    async with aiosqlite.connect(DB_PATH) as db:
        q = f"%{partial.lower()}%"
        async with db.execute(
            "SELECT title, artist FROM songs WHERE LOWER(title) LIKE ? OR LOWER(artist) LIKE ? LIMIT 10", (q, q)
        ) as cursor:
            rows = await cursor.fetchall()
            return [f"{r[0]} by {r[1]}" if r[1] else r[0] for r in rows]

async def find_song_by_title_artist(search_str):
    # Try to split 'title by artist'
    if " by " in search_str:
        title, artist = search_str.split(" by ", 1)
        async with aiosqlite.connect(DB_PATH) as db:
            async with db.execute(
                "SELECT title, artist, url FROM songs WHERE LOWER(title)=? AND LOWER(artist)=? LIMIT 1",
                (title.strip().lower(), artist.strip().lower()),
            ) as cursor:
                row = await cursor.fetchone()
                return row
    # Fallback to fuzzy search
    return await find_song_by_query(search_str)

async def add_song(title, artist, url):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("INSERT INTO songs (title, artist, url) VALUES (?, ?, ?)", (title, artist, url))
        await db.commit()

# --- Music playback ---

async def play_song(voice_client, url):
    ydl_opts = {
        "format": "bestaudio/best",
        "quiet": True,
        "no_warnings": True,
        "default_search": "ytsearch",
        "extract_flat": "in_playlist"
    }
    loop = asyncio.get_event_loop()
    with yt_dlp.YoutubeDL(ydl_opts) as ydl:
        info = await loop.run_in_executor(None, lambda: ydl.extract_info(url, download=False))
        if "entries" in info:
            info = info["entries"][0]
        audio_url = info["url"]
    source = await discord.FFmpegOpusAudio.from_probe(audio_url)
    voice_client.play(source)

# --- Slash commands using app_commands directly ---

@tree.command(name="play", description="Play a song by name or artist")
@app_commands.describe(query="Song title or artist")
async def play(interaction: discord.Interaction, query: str):
    await interaction.response.defer()
    song = await find_song_by_title_artist(query)
    if not song:
        await interaction.followup.send(f"No song found for '{query}'.")
        return

    title, artist, url = song
    member = interaction.user
    # Get member's voice state (use .guild and .guild.voice_client for correct context)
    if hasattr(member, "voice") and member.voice and member.voice.channel:
        channel = member.voice.channel
    else:
        await interaction.followup.send("You are not in a voice channel.")
        return

    if interaction.guild.voice_client:
        vc = interaction.guild.voice_client
        await vc.move_to(channel)
    else:
        vc = await channel.connect()
    if vc.is_playing():
        vc.stop()
    await play_song(vc, url)
    await interaction.followup.send(f"Now playing: **{title}** by **{artist or 'Unknown'}**")

@play.autocomplete("query")
async def play_autocomplete(interaction: discord.Interaction, current: str):
    if not current:
        return []
    choices = await find_song_choices(current)
    return [
        app_commands.Choice(name=choice, value=choice) for choice in choices
    ][:10]

@tree.command(name="addsong", description="Add a new song to the database")
@app_commands.describe(title="Song title", artist="Artist", url="YouTube or direct audio URL")
async def addsong(interaction: discord.Interaction, title: str, artist: str, url: str):
    await add_song(title, artist, url)
    await interaction.response.send_message(f"Added **{title}** by **{artist}** to the song database.", ephemeral=True)

@bot.event
async def on_ready():
    await db_init()
    await tree.sync()
    print(f"Logged in as {bot.user}")

if __name__ == "__main__":
    bot.run(TOKEN)