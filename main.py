import os
import sys

# audioop modülü için geçici çözüm
try:
    import audioop
except ImportError:
    # audioop modülü yoksa basit bir mock oluştur
    class MockAudioop:
        def __init__(self):
            pass
        def __getattr__(self, name):
            def mock_func(*args, **kwargs):
                return b'\x00' * 1024  # Sessizlik döndür
            return mock_func
    
    sys.modules['audioop'] = MockAudioop()

# Şimdi normal import'lar...
import discord
from discord.ext import commands
# ... diğer import'lar

# Render.com için HTTP server eklentisi
from flask import Flask
import threading
import os

# Flask app (Render.com için gerekli)
app = Flask(__name__)

@app.route('/')
def home():
    return "Discord Music Bot is running! 🎵"

@app.route('/health')
def health():
    return {"status": "healthy", "bot": str(bot.user) if bot.user else "not ready"}

def run_flask():
    port = int(os.environ.get('PORT', 5000))
    app.run(host='0.0.0.0', port=port, debug=False)

# Orijinal bot kodunuz burada (import'lar dahil)
import discord
from discord.ext import commands
import asyncio
import yt_dlp as youtube_dl
import spotipy
from spotipy.oauth2 import SpotifyClientCredentials
import re
from collections import deque
import random
import logging

# Logging ayarları
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Environment variables
DISCORD_TOKEN = os.getenv('DISCORD_TOKEN')
SPOTIFY_CLIENT_ID = os.getenv('SPOTIFY_CLIENT_ID') 
SPOTIFY_CLIENT_SECRET = os.getenv('SPOTIFY_CLIENT_SECRET')

# Bot ayarları
intents = discord.Intents.default()
intents.message_content = True
bot = commands.Bot(command_prefix='!', intents=intents)

# Spotify ayarları
try:
    if SPOTIFY_CLIENT_ID and SPOTIFY_CLIENT_SECRET:
        spotify = spotipy.Spotify(client_credentials_manager=SpotifyClientCredentials(
            client_id=SPOTIFY_CLIENT_ID,
            client_secret=SPOTIFY_CLIENT_SECRET
        ))
    else:
        spotify = None
        logger.warning("Spotify credentials bulunamadı!")
except Exception as e:
    logger.error(f"Spotify ayarları hatası: {e}")
    spotify = None

# YouTube DL ayarları
ytdl_format_options = {
    'format': 'bestaudio/best',
    'outtmpl': '%(extractor)s-%(id)s-%(title)s.%(ext)s',
    'restrictfilenames': True,
    'noplaylist': True,
    'nocheckcertificate': True,
    'ignoreerrors': False,
    'logtostderr': False,
    'quiet': True,
    'no_warnings': True,
    'default_search': 'auto',
    'source_address': '0.0.0.0'
}

# Ses efektleri için FFmpeg ayarları
def get_ffmpeg_options(effect=None):
    """Ses efektine göre FFmpeg ayarları döndür"""
    base_options = '-reconnect 1 -reconnect_streamed 1 -reconnect_delay_max 5'
    
    effects = {
        'bassboost': '-af "bass=g=10"',
        'nightcore': '-af "asetrate=44100*1.25,atempo=1.25"',
        'slowed': '-af "asetrate=44100*0.8,atempo=0.8"',
        'vaporwave': '-af "asetrate=44100*0.9,atempo=0.9,tremolo=5:0.7"',
        '8d': '-af "apulsator=hz=0.125"',
        'echo': '-af "aecho=0.8:0.9:1000:0.3"',
        'treble': '-af "treble=g=5"',
        'normal': '-vn'
    }
    
    if effect and effect in effects:
        return {
            'before_options': base_options,
            'options': effects[effect]
        }
    else:
        return {
            'before_options': base_options,
            'options': '-vn'
        }

ytdl = youtube_dl.YoutubeDL(ytdl_format_options)

# Guild bazlı ayarlar
music_queues = {}
current_songs = {}
repeat_modes = {}  # 0: kapalı, 1: şarkı tekrarı, 2: sıra tekrarı
music_history = {}  # Müzik geçmişi
sound_effects = {}  # Aktif ses efektleri

def get_queue(guild_id):
    """Guild için queue al, yoksa oluştur"""
    if guild_id not in music_queues:
        music_queues[guild_id] = deque()
    return music_queues[guild_id]

def get_history(guild_id):
    """Guild için geçmiş al, yoksa oluştur"""
    if guild_id not in music_history:
        music_history[guild_id] = deque(maxlen=20)  # Son 20 şarkı
    return music_history[guild_id]

def get_repeat_mode(guild_id):
    """Guild için tekrar modunu al"""
    return repeat_modes.get(guild_id, 0)

def get_sound_effect(guild_id):
    """Guild için aktif ses efektini al"""
    return sound_effects.get(guild_id, 'normal')

async def cleanup_guild_data(guild_id):
    """Kullanılmayan guild verilerini temizle"""
    if guild_id in music_queues:
        music_queues[guild_id].clear()
    if guild_id in current_songs:
        del current_songs[guild_id]
    if guild_id in repeat_modes:
        del repeat_modes[guild_id]
    if guild_id in sound_effects:
        del sound_effects[guild_id]
    if guild_id in music_history:
        del music_history[guild_id]
    logger.info(f"Guild {guild_id} verileri temizlendi")

class YTDLSource(discord.PCMVolumeTransformer):
    def __init__(self, source, *, data, volume=0.5):
        super().__init__(source, volume)
        self.data = data
        self.title = data.get('title')
        self.url = data.get('url')
        self.duration = data.get('duration')
        self.webpage_url = data.get('webpage_url')

    @classmethod
    async def from_url(cls, url, *, loop=None, stream=False, effect='normal'):
        loop = loop or asyncio.get_event_loop()
        try:
            data = await loop.run_in_executor(None, lambda: ytdl.extract_info(url, download=not stream))
            
            if 'entries' in data:
                data = data['entries'][0]
            
            filename = data['url'] if stream else ytdl.prepare_filename(data)
            ffmpeg_options = get_ffmpeg_options(effect)
            
            return cls(discord.FFmpegPCMAudio(filename, **ffmpeg_options), data=data)
        except Exception as e:
            logger.error(f"YTDL Hatası: {e}")
            raise e

def extract_spotify_id(url):
    """Spotify URL'den playlist/track ID'sini çıkar"""
    if 'playlist' in url:
        return url.split('/')[-1].split('?')[0], 'playlist'
    elif 'track' in url:
        return url.split('/')[-1].split('?')[0], 'track'
    elif 'album' in url:
        return url.split('/')[-1].split('?')[0], 'album'
    return None, None

async def get_spotify_tracks(url):
    """Spotify playlist/album/track'ten şarkı listesi al"""
    if not spotify:
        return []
    
    try:
        spotify_id, content_type = extract_spotify_id(url)
        if not spotify_id:
            return []
        
        tracks = []
        
        if content_type == 'playlist':
            results = spotify.playlist_tracks(spotify_id)
            for item in results['items']:
                if item['track']:
                    track = item['track']
                    artist = track['artists'][0]['name']
                    name = track['name']
                    search_term = f"{artist} {name}"
                    tracks.append(search_term)
                    
        elif content_type == 'album':
            results = spotify.album_tracks(spotify_id)
            album = spotify.album(spotify_id)
            artist = album['artists'][0]['name']
            for track in results['items']:
                search_term = f"{artist} {track['name']}"
                tracks.append(search_term)
                
        elif content_type == 'track':
            track = spotify.track(spotify_id)
            artist = track['artists'][0]['name']
            name = track['name']
            search_term = f"{artist} {name}"
            tracks.append(search_term)
            
        return tracks
    except Exception as e:
        logger.error(f"Spotify error: {e}")
        return []

async def play_next(ctx):
    """Sıradaki şarkıyı çal (tekrar modunu destekler)"""
    guild_id = ctx.guild.id
    queue = get_queue(guild_id)
    history = get_history(guild_id)
    repeat_mode = get_repeat_mode(guild_id)
    effect = get_sound_effect(guild_id)
    
    if not ctx.voice_client:
        return
    
    # Tekrar modlarını kontrol et
    if repeat_mode == 1 and guild_id in current_songs:
        # Şarkı tekrarı - aynı şarkıyı tekrar çal
        current_song_data = current_songs[guild_id]
        try:
            player = await YTDLSource.from_url(current_song_data.webpage_url or current_song_data.data.get('webpage_url', ''), 
                                             loop=bot.loop, stream=True, effect=effect)
            current_songs[guild_id] = player
            ctx.voice_client.play(player, after=lambda e: asyncio.run_coroutine_threadsafe(play_next(ctx), bot.loop))
            await ctx.send(f'🔂 Tekrarlanıyor: **{player.title}**')
        except Exception as e:
            logger.error(f'Tekrarlama hatası: {e}')
            await ctx.send(f'❌ Tekrarlama hatası: {e}')
        return
    
    # Sırada şarkı var mı kontrol et
    if queue:
        next_song = queue.popleft()
        
        # Sıra tekrarı aktifse şarkıyı sıranın sonuna ekle
        if repeat_mode == 2:
            queue.append(next_song)
            
    elif repeat_mode == 2 and history:
        # Sıra boş ama sıra tekrarı aktif - geçmişten yeniden başla
        next_song = list(history)[0]
        # Geçmişten sıraya şarkıları ekle (ilk şarkı hariç)
        for song in list(history)[1:]:
            queue.append(song)
        queue.append(next_song)  # İlk şarkıyı da sıranın sonuna ekle
    else:
        return  # Çalacak şarkı yok
    
    try:
        player = await YTDLSource.from_url(next_song, loop=bot.loop, stream=True, effect=effect)
        current_songs[guild_id] = player
        
        # Geçmişe ekle
        history.append(next_song)
        
        ctx.voice_client.play(player, after=lambda e: asyncio.run_coroutine_threadsafe(play_next(ctx), bot.loop))
        
        # Tekrar modu gösterimi
        repeat_emoji = ""
        if repeat_mode == 1:
            repeat_emoji = "🔂 "
        elif repeat_mode == 2:
            repeat_emoji = "🔁 "
            
        effect_emoji = ""
        if effect != 'normal':
            effect_emoji = f"🎛️[{effect}] "
            
        await ctx.send(f'🎵 {repeat_emoji}{effect_emoji}Şu an çalıyor: **{player.title}**')
        
    except Exception as e:
        logger.error(f'Play next hatası: {e}')
        await ctx.send(f'❌ Hata oluştu: {e}')
        await play_next(ctx)

# Event handlers
@bot.event
async def on_ready():
    logger.info(f'{bot.user} çevrimiçi!')
    logger.info('Komutlar: !mhelp ile tüm komutları görebilirsiniz')
    logger.info(f'Bot {len(bot.guilds)} sunucuda aktif')
    
    # Bot durumunu ayarla
    try:
        await bot.change_presence(
            activity=discord.Activity(
                type=discord.ActivityType.listening, 
                name="!mhelp | 🎵"
            )
        )
        logger.info("Bot durumu ayarlandı")
    except Exception as e:
        logger.error(f"Presence ayarlama hatası: {e}")

@bot.event
async def on_command_error(ctx, error):
    if isinstance(error, commands.CommandNotFound):
        await ctx.send('❌ Komut bulunamadı! `!mhelp` ile komutları görebilirsin.')
    elif isinstance(error, commands.MissingRequiredArgument):
        await ctx.send('❌ Eksik parametre! Komutu doğru kullandığından emin ol.')
    else:
        logger.error(f'Command Error: {error}')
        await ctx.send('❌ Bir hata oluştu!')

@bot.event
async def on_error(event, *args, **kwargs):
    logger.error(f'Event Error: {event}')

@bot.event
async def on_voice_state_update(member, before, after):
    """Bot yalnız kaldığında otomatik çık"""
    if member == bot.user:
        return
        
    voice_client = member.guild.voice_client
    if voice_client and len(voice_client.channel.members) == 1:
        await asyncio.sleep(300)  # 5 dakika bekle
        if voice_client.is_connected() and len(voice_client.channel.members) == 1:
            await voice_client.disconnect()
            await cleanup_guild_data(member.guild.id)
            logger.info(f"Bot {member.guild.name} sunucusunda yalnız kaldığı için ayrıldı.")

# Komutlar (kısaltılmış - tüm komutları ekleyebilirsiniz)
@bot.command()
async def join(ctx):
    """Ses kanalına katıl"""
    try:
        if ctx.author.voice:
            channel = ctx.author.voice.channel
            if ctx.voice_client is None:
                await channel.connect()
                await ctx.send(f'✅ {channel} kanalına katıldım!')
                logger.info(f"{ctx.guild.name} - {channel} kanalına katıldı")
            else:
                await ctx.voice_client.move_to(channel)
                await ctx.send(f'🔄 {channel} kanalına geçtim!')
                logger.info(f"{ctx.guild.name} - {channel} kanalına geçti")
        else:
            await ctx.send('❌ Önce bir ses kanalına katılman gerekiyor!')
    except Exception as e:
        logger.error(f'Join hatası: {e}')
        await ctx.send('❌ Ses kanalına katılırken hata oluştu!')

# Diğer tüm komutlarınızı buraya ekleyin...
# (Önceki artifact'taki tüm komutları kopyalayın)

# Bot'u başlat
if __name__ == "__main__":
    if not DISCORD_TOKEN:
        logger.error("DISCORD_TOKEN environment variable bulunamadı!")
    else:
        try:
            logger.info("Flask server ve Discord bot başlatılıyor...")
            # Flask sunucusunu ayrı thread'de çalıştır
            flask_thread = threading.Thread(target=run_flask)
            flask_thread.daemon = True
            flask_thread.start()
            
            # Discord bot'u başlat
            bot.run(DISCORD_TOKEN)
        except Exception as e:
            logger.error(f"Bot başlatılamadı: {e}")
