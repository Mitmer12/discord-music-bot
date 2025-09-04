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

# YouTube DL ayarları - bu bölümü bulun ve değiştirin
ytdl_format_options = {
    'format': 'bestaudio[ext=m4a]/bestaudio/best',
    'outtmpl': '%(extractor)s-%(id)s-%(title)s.%(ext)s',
    'restrictfilenames': True,
    'noplaylist': True,
    'nocheckcertificate': True,
    'ignoreerrors': False,
    'logtostderr': False,
    'quiet': True,
    'no_warnings': True,
    'default_search': 'ytsearch',
    'source_address': '0.0.0.0',
    'force_json': True,
    'extract_flat': False,
    'writethumbnail': False,
    'writeinfojson': False
}

# Ses efektleri için FFmpeg ayarları
def get_ffmpeg_options(effect=None):
    """Ses efektine göre FFmpeg ayarları döndür"""
    base_options = '-reconnect 1 -reconnect_streamed 1 -reconnect_delay_max 5 -vn'
    
    if effect and effect != 'normal':
        return {
            'before_options': base_options,
            'options': '-f wav'
        }
    else:
        return {
            'before_options': base_options,
            'options': '-f wav'
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

@bot.command()
async def leave(ctx):
    """Ses kanalından çık"""
    try:
        if ctx.voice_client:
            guild_id = ctx.guild.id
            await cleanup_guild_data(guild_id)
            await ctx.voice_client.disconnect()
            await ctx.send('👋 Ses kanalından çıktım!')
            logger.info(f"{ctx.guild.name} sunucusundan ayrıldı")
        else:
            await ctx.send('❌ Zaten ses kanalında değilim!')
    except Exception as e:
        logger.error(f'Leave hatası: {e}')
        await ctx.send('❌ Ses kanalından çıkarken hata oluştu!')

@bot.command(aliases=['play'])
async def p(ctx, *, search):
    """YouTube'dan müzik çal veya sıraya ekle"""
    try:
        if not ctx.voice_client:
            if ctx.author.voice:
                await ctx.author.voice.channel.connect()
                logger.info(f"{ctx.guild.name} - {ctx.author.voice.channel} kanalına otomatik katıldı")
            else:
                await ctx.send('❌ Önce bir ses kanalına katılman gerekiyor!')
                return

        guild_id = ctx.guild.id
        queue = get_queue(guild_id)
        history = get_history(guild_id)
        effect = get_sound_effect(guild_id)
        
        async with ctx.typing():
            # Spotify URL kontrolü
            if 'open.spotify.com' in search:
                if not spotify:
                    await ctx.send('❌ Spotify entegrasyonu mevcut değil!')
                    return
                    
                await ctx.send('🎵 Spotify içeriği tespit edildi! İşleniyor...')
                tracks = await get_spotify_tracks(search)
                
                if not tracks:
                    await ctx.send('❌ Spotify içeriği alınamadı!')
                    return
                
                if ctx.voice_client.is_playing() or queue:
                    for track in tracks:
                        queue.append(track)
                    await ctx.send(f'✅ Spotify\'dan {len(tracks)} şarkı sıraya eklendi!')
                    logger.info(f"{ctx.guild.name} - Spotify'dan {len(tracks)} şarkı eklendi")
                else:
                    first_track = tracks[0]
                    remaining_tracks = tracks[1:]
                    
                    player = await YTDLSource.from_url(first_track, loop=bot.loop, stream=True, effect=effect)
                    current_songs[guild_id] = player
                    history.append(first_track)
                    
                    ctx.voice_client.play(player, after=lambda e: asyncio.run_coroutine_threadsafe(play_next(ctx), bot.loop))
                    
                    for track in remaining_tracks:
                        queue.append(track)
                    
                    effect_emoji = f"🎛️[{effect}] " if effect != 'normal' else ""
                    await ctx.send(f'🎵 {effect_emoji}Çalıyor: **{player.title}**\n📝 {len(remaining_tracks)} şarkı daha sıraya eklendi!')
                    logger.info(f"{ctx.guild.name} - Spotify şarkı çalıyor: {player.title}")
                return
            
            if ctx.voice_client.is_playing() or queue:
                queue.append(search)
                try:
                    data = await bot.loop.run_in_executor(None, lambda: ytdl.extract_info(search, download=False))
                    if 'entries' in data:
                        data = data['entries'][0]
                    title = data.get('title', search)
                    await ctx.send(f'📝 Sıraya eklendi: **{title}** (Sıra: {len(queue)})')
                    logger.info(f"{ctx.guild.name} - Sıraya eklendi: {title}")
                except:
                    await ctx.send(f'📝 Sıraya eklendi: **{search}** (Sıra: {len(queue)})')
                    logger.info(f"{ctx.guild.name} - Sıraya eklendi: {search}")
            else:
                player = await YTDLSource.from_url(search, loop=bot.loop, stream=True, effect=effect)
                current_songs[guild_id] = player
                history.append(search)
                
                ctx.voice_client.play(player, after=lambda e: asyncio.run_coroutine_threadsafe(play_next(ctx), bot.loop))
                
                effect_emoji = f"🎛️[{effect}] " if effect != 'normal' else ""
                await ctx.send(f'🎵 {effect_emoji}Şu an çalıyor: **{player.title}**')
                logger.info(f"{ctx.guild.name} - Çalıyor: {player.title}")
                
    except Exception as e:
        logger.error(f'Play komutu hatası: {e}')
        await ctx.send(f'❌ Bir hata oluştu: {e}')

@bot.command()
async def skip(ctx):
    """Sıradaki şarkıya geç"""
    try:
        if ctx.voice_client and ctx.voice_client.is_playing():
            ctx.voice_client.stop()
            await ctx.send('⏭️ Şarkı atlandı!')
            logger.info(f"{ctx.guild.name} - Şarkı atlandı")
        else:
            await ctx.send('❌ Şu an çalan şarkı yok!')
    except Exception as e:
        logger.error(f'Skip komutu hatası: {e}')
        await ctx.send('❌ Şarkı atlarken hata oluştu!')

@bot.command()
async def stop(ctx):
    """Müziği durdur ve sırayı temizle"""
    try:
        if ctx.voice_client:
            guild_id = ctx.guild.id
            queue = get_queue(guild_id)
            queue.clear()
            repeat_modes[guild_id] = 0
            ctx.voice_client.stop()
            if guild_id in current_songs:
                del current_songs[guild_id]
            await ctx.send('⏹️ Müzik durduruldu ve sıra temizlendi!')
            logger.info(f"{ctx.guild.name} - Müzik durduruldu")
    except Exception as e:
        logger.error(f'Stop komutu hatası: {e}')
        await ctx.send('❌ Müzik durdurulurken hata oluştu!')

@bot.command()
async def pause(ctx):
    """Müziği duraklat"""
    try:
        if ctx.voice_client and ctx.voice_client.is_playing():
            ctx.voice_client.pause()
            await ctx.send('⏸️ Müzik duraklatıldı!')
            logger.info(f"{ctx.guild.name} - Müzik duraklatıldı")
        else:
            await ctx.send('❌ Şu an çalan şarkı yok!')
    except Exception as e:
        logger.error(f'Pause komutu hatası: {e}')
        await ctx.send('❌ Müzik duraklatılırken hata oluştu!')

@bot.command()
async def resume(ctx):
    """Müziği devam ettir"""
    try:
        if ctx.voice_client and ctx.voice_client.is_paused():
            ctx.voice_client.resume()
            await ctx.send('▶️ Müzik devam ediyor!')
            logger.info(f"{ctx.guild.name} - Müzik devam ettirildi")
        else:
            await ctx.send('❌ Duraklatılmış şarkı yok!')
    except Exception as e:
        logger.error(f'Resume komutu hatası: {e}')
        await ctx.send('❌ Müzik devam ettirilirken hata oluştu!')

@bot.command()
async def volume(ctx, vol: int = None):
    """Ses seviyesini ayarla (0-100)"""
    try:
        if not ctx.voice_client:
            await ctx.send('❌ Ses kanalında değilim!')
            return
        
        guild_id = ctx.guild.id
        if guild_id not in current_songs:
            await ctx.send('❌ Şu an çalan şarkı yok!')
            return
        
        if vol is None:
            current_vol = int(current_songs[guild_id].volume * 100)
            await ctx.send(f'🔊 Mevcut ses seviyesi: {current_vol}%')
            return
        
        if 0 <= vol <= 100:
            current_songs[guild_id].volume = vol / 100.0
            await ctx.send(f'🔊 Ses seviyesi {vol}% olarak ayarlandı!')
            logger.info(f"{ctx.guild.name} - Ses seviyesi {vol}% olarak ayarlandı")
        else:
            await ctx.send('❌ Ses seviyesi 0-100 arasında olmalı!')
    except Exception as e:
        logger.error(f'Volume komutu hatası: {e}')
        await ctx.send('❌ Ses seviyesi ayarlanırken hata oluştu!')

@bot.command()
async def np(ctx):
    """Şu an çalan şarkıyı göster"""
    try:
        guild_id = ctx.guild.id
        if guild_id in current_songs and ctx.voice_client and ctx.voice_client.is_playing():
            song = current_songs[guild_id]
            repeat_mode = get_repeat_mode(guild_id)
            effect = get_sound_effect(guild_id)
            
            embed = discord.Embed(title="🎵 Şu An Çalıyor", color=0x00ff00)
            embed.add_field(name="Şarkı", value=song.title, inline=False)
            
            if song.duration:
                mins, secs = divmod(song.duration, 60)
                embed.add_field(name="Süre", value=f"{int(mins):02d}:{int(secs):02d}", inline=True)
            
            embed.add_field(name="Ses Seviyesi", value=f"{int(song.volume * 100)}%", inline=True)
            
            repeat_text = ['Kapalı', 'Şarkı Tekrarı 🔂', 'Sıra Tekrarı 🔁'][repeat_mode]
            embed.add_field(name="Tekrar Modu", value=repeat_text, inline=True)
            
            effects_name = {
                'normal': 'Normal',
                'bassboost': 'Bass Boost 🎛️',
                'nightcore': 'Nightcore ⚡',
                'slowed': 'Slowed 🐌',
                'vaporwave': 'Vaporwave 🌸',
                '8d': '8D Audio 🎧',
                'echo': 'Echo 🔊',
                'treble': 'Treble Boost 📢'
            }
            embed.add_field(name="Ses Efekti", value=effects_name.get(effect, effect), inline=True)
            
            await ctx.send(embed=embed)
        else:
            await ctx.send('❌ Şu an çalan şarkı yok!')
    except Exception as e:
        logger.error(f'NP komutu hatası: {e}')
        await ctx.send('❌ Şarkı bilgileri alınırken hata oluştu!')

@bot.command(aliases=['queue'])
async def q(ctx):
    """Şarkı sırasını göster"""
    try:
        guild_id = ctx.guild.id
        queue = get_queue(guild_id)
        
        if not queue:
            await ctx.send('📝 Sıra boş!')
            return
        
        embed = discord.Embed(title="📝 Müzik Sırası", color=0x0099ff)
        
        for i, song in enumerate(list(queue)[:10], 1):
            if len(song) > 50:
                song_name = song[:47] + "..."
            else:
                song_name = song
            embed.add_field(name=f"{i}.", value=song_name, inline=False)
        
        if len(queue) > 10:
            embed.add_field(name="...", value=f"ve {len(queue) - 10} şarkı daha", inline=False)
        
        embed.set_footer(text=f"Toplam: {len(queue)} şarkı")
        await ctx.send(embed=embed)
    except Exception as e:
        logger.error(f'Queue komutu hatası: {e}')
        await ctx.send('❌ Sıra gösterilirken hata oluştu!')

@bot.command()
async def clear(ctx):
    """Şarkı sırasını temizle"""
    try:
        guild_id = ctx.guild.id
        queue = get_queue(guild_id)
        queue.clear()
        await ctx.send('🗑️ Sıra temizlendi!')
        logger.info(f"{ctx.guild.name} - Sıra temizlendi")
    except Exception as e:
        logger.error(f'Clear komutu hatası: {e}')
        await ctx.send('❌ Sıra temizlenirken hata oluştu!')

@bot.command()
async def shuffle(ctx):
    """Sırayı karıştır"""
    try:
        guild_id = ctx.guild.id
        queue = get_queue(guild_id)
        
        if len(queue) < 2:
            await ctx.send('❌ Karıştırılacak yeterli şarkı yok!')
            return
        
        queue_list = list(queue)
        random.shuffle(queue_list)
        queue.clear()
        queue.extend(queue_list)
        await ctx.send('🔀 Sıra karıştırıldı!')
        logger.info(f"{ctx.guild.name} - Sıra karıştırıldı")
    except Exception as e:
        logger.error(f'Shuffle komutu hatası: {e}')
        await ctx.send('❌ Sıra karıştırılırken hata oluştu!')

# TEKRAR MODU KOMUTLARI
@bot.command(aliases=['repeat', 'loop'])
async def r(ctx, mode=None):
    """Tekrar modunu ayarla (off/song/queue)"""
    try:
        guild_id = ctx.guild.id
        
        if mode is None:
            current_mode = get_repeat_mode(guild_id)
            modes = ['Kapalı', 'Şarkı Tekrarı', 'Sıra Tekrarı']
            await ctx.send(f'🔁 Mevcut tekrar modu: **{modes[current_mode]}**')
            return
        
        mode = mode.lower()
        if mode in ['off', 'kapalı', '0']:
            repeat_modes[guild_id] = 0
            await ctx.send('🔁 Tekrar modu **kapatıldı**!')
        elif mode in ['song', 'şarkı', '1']:
            repeat_modes[guild_id] = 1
            await ctx.send('🔂 **Şarkı tekrarı** aktif!')
        elif mode in ['queue', 'sıra', '2']:
            repeat_modes[guild_id] = 2
            await ctx.send('🔁 **Sıra tekrarı** aktif!')
        else:
            await ctx.send('❌ Geçersiz mod! Kullanım: `!r [off/song/queue]`')
    except Exception as e:
        logger.error(f'Repeat komutu hatası: {e}')
        await ctx.send('❌ Tekrar modu ayarlanırken hata oluştu!')

# SES EFEKTİ KOMUTLARI
@bot.command(aliases=['effect', 'fx'])
async def efekt(ctx, effect=None):
    """Ses efektini değiştir"""
    try:
        guild_id = ctx.guild.id
        
        available_effects = {
            'normal': 'Normal (efekt yok)',
            'bassboost': 'Bass Boost',
            'nightcore': 'Nightcore (hızlı + yüksek ses)',
            'slowed': 'Slowed (yavaş)',
            'vaporwave': 'Vaporwave',
            '8d': '8D Audio',
            'echo': 'Echo/Reverb',
            'treble': 'Treble Boost'
        }
        
        if effect is None:
            current_effect = get_sound_effect(guild_id)
            embed = discord.Embed(title="🎛️ Ses Efektleri", color=0xff6b35)
            embed.add_field(name="Mevcut Efekt", value=f"**{available_effects[current_effect]}**", inline=False)
            
            effects_list = '\n'.join([f"`{k}` - {v}" for k, v in available_effects.items()])
            embed.add_field(name="Mevcut Efektler", value=effects_list, inline=False)
            embed.add_field(name="Kullanım", value="`!efekt <efekt_adı>`", inline=False)
            
            await ctx.send(embed=embed)
            return
        
        effect = effect.lower()
        if effect in available_effects:
            sound_effects[guild_id] = effect
            await ctx.send(f'🎛️ Ses efekti **{available_effects[effect]}** olarak ayarlandı!')
            logger.info(f"{ctx.guild.name} - Ses efekti değişti: {effect}")
            
            if ctx.voice_client and ctx.voice_client.is_playing() and guild_id in current_songs:
                await ctx.send('🔄 Efekt uygulanıyor, şarkı yeniden başlatılıyor...')
                ctx.voice_client.stop()
        else:
            await ctx.send(f'❌ Geçersiz efekt! Kullanılabilir efektler: {", ".join(available_effects.keys())}')
    except Exception as e:
        logger.error(f'Efekt komutu hatası: {e}')
        await ctx.send('❌ Efekt ayarlanırken hata oluştu!')

# MÜZİK GEÇMİŞİ KOMUTLARI
@bot.command(aliases=['geçmiş', 'hist'])
async def history(ctx):
    """Müzik geçmişini göster"""
    try:
        guild_id = ctx.guild.id
        history = get_history(guild_id)
        
        if not history:
            await ctx.send('📜 Müzik geçmişi boş!')
            return
        
        embed = discord.Embed(title="📜 Müzik Geçmişi", color=0x9b59b6)
        
        recent_songs = list(history)[-10:]
        recent_songs.reverse()
        
        for i, song in enumerate(recent_songs, 1):
            if len(song) > 50:
                song_name = song[:47] + "..."
            else:
                song_name = song
            embed.add_field(name=f"{i}.", value=song_name, inline=False)
        
        if len(history) > 10:
            embed.add_field(name="...", value=f"ve {len(history) - 10} şarkı daha", inline=False)
        
        embed.set_footer(text=f"Toplam: {len(history)} şarkı")
        await ctx.send(embed=embed)
    except Exception as e:
        logger.error(f'History komutu hatası: {e}')
        await ctx.send('❌ Geçmiş gösterilirken hata oluştu!')

@bot.command(aliases=['playback', 'pb'])
async def geriekle(ctx, index: int = 1):
    """Geçmişten şarkı sıraya ekle"""
    try:
        guild_id = ctx.guild.id
        history = get_history(guild_id)
        queue = get_queue(guild_id)
        effect = get_sound_effect(guild_id)
        
        if not ctx.voice_client:
            if ctx.author.voice:
                await ctx.author.voice.channel.connect()
            else:
                await ctx.send('❌ Önce bir ses kanalına katılman gerekiyor!')
                return
        
        if not history:
            await ctx.send('❌ Müzik geçmişi boş!')
            return
        
        if 1 <= index <= len(history):
            song = list(history)[-(index)]
            
            try:
                data = await bot.loop.run_in_executor(None, lambda: ytdl.extract_info(song, download=False))
                if 'entries' in data:
                    data = data['entries'][0]
                title = data.get('title', song)
            except:
                title = song
            
            if not ctx.voice_client.is_playing() and not queue:
                async with ctx.typing():
                    player = await YTDLSource.from_url(song, loop=bot.loop, stream=True, effect=effect)
                    current_songs[guild_id] = player
                    history.append(song)
                    
                    ctx.voice_client.play(player, after=lambda e: asyncio.run_coroutine_threadsafe(play_next(ctx), bot.loop))
                    
                    effect_emoji = f"🎛️[{effect}] " if effect != 'normal' else ""
                    await ctx.send(f'📜▶️ Geçmişten çalıyor: {effect_emoji}**{title}**')
                    logger.info(f"{ctx.guild.name} - Geçmişten çalıyor: {title}")
            else:
                queue.append(song)
                await ctx.send(f'📜➕ Geçmişten sıraya eklendi: **{title}** (Sıra: {len(queue)})')
                logger.info(f"{ctx.guild.name} - Geçmişten sıraya eklendi: {title}")
                
        else:
            await ctx.send(f'❌ Geçersiz index! 1-{len(history)} arası bir sayı girin.')
    except Exception as e:
        logger.error(f'Geriekle komutu hatası: {e}')
        await ctx.send('❌ Geçmişten ekleme sırasında hata oluştu!')

@bot.command(aliases=['spotify'])
async def sp(ctx, *, url):
    """Spotify playlist/album/şarkı çal"""
    try:
        if 'open.spotify.com' not in url:
            await ctx.send('❌ Geçerli bir Spotify URL\'si girin!')
            return
        
        await ctx.invoke(bot.get_command('p'), search=url)
    except Exception as e:
        logger.error(f'Spotify komutu hatası: {e}')
        await ctx.send('❌ Spotify URL\'si işlenirken hata oluştu!')

@bot.command(aliases=['musikhelp'])
async def mhelp(ctx):
    """Tüm müzik komutlarını göster"""
    try:
        embed = discord.Embed(title="🎵 Müzik Bot Komutları", color=0xff9900)
        
        # Temel komutlar - tek field'da
        basic_text = """
`!join` - Ses kanalına katıl
`!leave` - Ses kanalından çık  
`!p <şarkı>` - YouTube'dan müzik çal
`!skip` - Sıradaki şarkıya geç
`!stop` - Müziği durdur
`!pause` / `!resume` - Duraklat / Devam ettir
        """
        embed.add_field(name="🎵 Temel Komutlar", value=basic_text, inline=False)
        
        # Kontrol komutları
        control_text = """
`!q` - Şarkı sırasını göster
`!clear` - Sırayı temizle
`!shuffle` - Sırayı karıştır
`!volume <0-100>` - Ses seviyesi ayarla
`!np` - Şu an çalan şarkıyı göster
        """
        embed.add_field(name="📝 Kontrol", value=control_text, inline=False)
        
        # İleri özellikler
        advanced_text = """
`!r` - Tekrar modunu göster/ayarla
`!efekt` - Ses efektlerini göster
`!history` - Geçmiş şarkıları göster
`!sp <url>` - Spotify URL çal
        """
        embed.add_field(name="⚡ İleri Özellikler", value=advanced_text, inline=False)
        
        embed.set_footer(text="Prefix: ! | Örnek: !p never gonna give you up")
        
        await ctx.send(embed=embed)
    except Exception as e:
        # Embed hatası varsa basit mesaj gönder
        help_text = """
🎵 **Müzik Bot Komutları:**

**Temel:** !join, !leave, !p <şarkı>, !skip, !stop, !pause, !resume
**Kontrol:** !q, !clear, !shuffle, !volume, !np  
**İleri:** !r, !efekt, !history, !sp <url>

**Kullanım:** !p never gonna give you up
        """
        await ctx.send(help_text)
        logger.error(f'Help komutu hatası: {e}')

# Diğer tüm komutlarınızı buraya ekleyin...
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
