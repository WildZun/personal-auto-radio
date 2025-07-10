import os
import subprocess
from pathlib import Path
from typing import List, Tuple, Optional
from dotenv import load_dotenv
import spotipy
from spotipy.oauth2 import SpotifyClientCredentials
import re
import threading
import time
from dataclasses import dataclass
import sys
import queue

# Utility to sanitize filenames
def sanitize_filename(name: str) -> str:
    return re.sub(r'[<>:"/\\|?*]', '_', name).strip()

@dataclass
class DownloadProgress:
    current_item: str = ""
    total_items: int = 0
    completed_items: int = 0
    skipped_items: int = 0
    current_track: str = ""
    failed_items: List[str] = None
    current_track_progress: float = 0.0
    current_track_status: str = ""

    def __post_init__(self):
        if self.failed_items is None:
            self.failed_items = []

    def get_global_progress_bar(self, width: int = 40) -> str:
        pct = (self.completed_items / self.total_items * 100) if self.total_items else 0
        filled = int(width * self.completed_items / max(self.total_items,1))
        bar = '█'*filled + '░'*(width-filled)
        return f"[{bar}] {self.completed_items}/{self.total_items} ({pct:.1f}%)"

    def get_track_progress_bar(self, width: int = 30) -> str:
        filled = int(width * self.current_track_progress / 100)
        bar = '█'*filled + '░'*(width-filled)
        return f"[{bar}] {self.current_track_progress:.1f}%"

class SpotifyDownloader:
    def __init__(self):
        load_dotenv()
        self.script_directory = Path(__file__).parent
        self.music_directory = self.script_directory / 'Music'
        self.music_directory.mkdir(exist_ok=True)
        self.sp = self._init_spotify_client()
        self.progress = DownloadProgress()
        self._stop_flag = False
        self._lock = threading.Lock()

    def _init_spotify_client(self) -> spotipy.Spotify:
        cid = os.getenv('SPOTIFY_CLIENT_ID')
        cs = os.getenv('SPOTIFY_CLIENT_SECRET')
        if not cid or not cs:
            raise ValueError('SPOTIFY_CLIENT_ID et SPOTIFY_CLIENT_SECRET requis')
        auth = SpotifyClientCredentials(client_id=cid, client_secret=cs)
        return spotipy.Spotify(auth_manager=auth)

    def _display_progress(self):
        lines = 0
        while not self._stop_flag:
            with self._lock:
                if lines > 0:
                    for _ in range(lines): print("\033[1A\033[K", end='')
                lines = 0
                print(f"🌍 Global: {self.progress.get_global_progress_bar()}")
                lines += 1
                if self.progress.skipped_items > 0:
                    print(f"⏭️ Ignorés: {self.progress.skipped_items}")
                    lines += 1
                if self.progress.current_track:
                    print(f"🎵 Titre:  {self.progress.get_track_progress_bar()} {self.progress.current_track}")
                    lines += 1
                if self.progress.current_track_status:
                    print(f"📊 Status: {self.progress.current_track_status}")
                    lines += 1
                sys.stdout.flush()
            time.sleep(0.3)

    def _start_progress(self) -> threading.Thread:
        self._stop_flag = False
        t = threading.Thread(target=self._display_progress, daemon=True)
        t.start()
        return t

    def _stop_progress(self):
        self._stop_flag = True
        time.sleep(0.5)
        print()

    def _update_progress(self, track=None, prog=None, status=None):
        with self._lock:
            if track is not None: self.progress.current_track = track
            if prog is not None: self.progress.current_track_progress = prog
            if status is not None: self.progress.current_track_status = status

    def _extract_spotify_info(self, url: str) -> Tuple[str,str]:
        if 'spotify.com' not in url:
            raise ValueError('URL Spotify invalide')
        pattern = r'spotify\.com/([^/]+)/([^/?]+)'
        m = re.search(pattern, url)
        if not m: raise ValueError("Impossible d'extraire info URL")
        t,id_ = m.groups()
        if t not in ['album','playlist']: raise ValueError('Le type doit être album ou playlist')
        return t,id_

    def _get_playlist_info(self, playlist_id: str) -> List[Tuple[str,str,str,str]]:
        plist = self.sp.playlist(playlist_id)
        total = plist['tracks']['total']
        items=[]; offset=0
        while offset < total:
            batch = self.sp.playlist_tracks(playlist_id, offset=offset, limit=100,
                                            fields='items(track(id,name,artists,album)),total')
            for it in batch['items']:
                tr = it['track']
                if not tr or not tr.get('id'): continue
                artist = sanitize_filename(tr['artists'][0]['name'])
                album = sanitize_filename(tr['album']['name'])
                items.append((artist,album,tr['id'],'playlist'))
            offset += 100
            time.sleep(0.1)
        return items

    def _get_album_info(self, album_id: str) -> List[Tuple[str,str,str,str]]:
        album = self.sp.album(album_id)
        artist = sanitize_filename(album['artists'][0]['name'])
        name = sanitize_filename(album['name'])
        total = album['tracks']['total']
        items=[]; offset=0
        while offset < total:
            batch = self.sp.album_tracks(album_id, offset=offset, limit=50)
            for tr in batch['items']:
                if not tr.get('id'): continue
                items.append((artist,name,tr['id'],'album'))
            offset += 50
            time.sleep(0.1)
        return items

    def parse_spotify_item(self, url: str) -> List[Tuple[str,str,str,str]]:
        t,id_ = self._extract_spotify_info(url)
        return self._get_album_info(id_) if t=='album' else self._get_playlist_info(id_)

    def _file_exists(self, artist: str, album: str, track_id: str) -> bool:
        """
        Vérifie si un fichier de musique existe déjà.
        Recherche dans le dossier artist/album/ plusieurs formats possibles.
        """
        try:
            # Obtenir le titre de la piste
            track_info = self.sp.track(track_id)
            title = sanitize_filename(track_info['name'])
            
            # Chemins à vérifier
            artist_dir = self.music_directory / artist
            album_dir = artist_dir / album
            
            if not album_dir.exists():
                return False
            
            # Formats de fichiers possibles
            possible_filenames = [
                f"{title}.mp3",
                f"{artist} - {title}.mp3",
                f"{sanitize_filename(track_info['name'])}.mp3",
                f"{artist} - {sanitize_filename(track_info['name'])}.mp3"
            ]
            
            # Vérifier tous les fichiers MP3 dans le dossier
            for mp3_file in album_dir.glob("*.mp3"):
                filename = mp3_file.name
                # Vérification exacte
                if filename in possible_filenames:
                    return True
                # Vérification approximative (enlever caractères spéciaux)
                clean_filename = re.sub(r'[^\w\s-]', '', filename.lower())
                clean_title = re.sub(r'[^\w\s-]', '', title.lower())
                if clean_title in clean_filename:
                    return True
            
            return False
            
        except Exception as e:
            print(f"Erreur lors de la vérification du fichier: {e}")
            return False

    def _read_output(self, pipe, output_queue):
        """Thread pour lire la sortie du processus en temps réel"""
        try:
            while True:
                line = pipe.readline()
                if not line:
                    break
                output_queue.put(line.strip())
        except Exception:
            pass
        finally:
            pipe.close()

    def _download_spotdl(self, url: str, cwd: Path, display: str) -> bool:
        """Télécharge une piste avec spotdl et affiche la progression en temps réel"""
        cmd = ['spotdl', 'download', url, '--format', 'mp3', '--bitrate', '320k', '--overwrite', 'skip']
        self._update_progress(track=display, prog=0, status='🔄 Démarrage')
        
        try:
            # Démarrer le processus
            proc = subprocess.Popen(
                cmd, 
                cwd=cwd, 
                stdout=subprocess.PIPE, 
                stderr=subprocess.STDOUT,  # Rediriger stderr vers stdout
                text=True, 
                bufsize=1,
                universal_newlines=True
            )
            
            # Queue pour recevoir les lignes de sortie
            output_queue = queue.Queue()
            
            # Thread pour lire la sortie
            reader_thread = threading.Thread(
                target=self._read_output, 
                args=(proc.stdout, output_queue),
                daemon=True
            )
            reader_thread.start()
            
            last_progress = 0
            progress_patterns = [
                r'(\d+(?:\.\d+)?)%',  # Format standard: 45.2%
                r'(\d+)/\d+\s*\((\d+(?:\.\d+)?)%\)',  # Format avec ratio: 45/100 (45%)
                r'Downloaded\s+(\d+(?:\.\d+)?)%',  # Format avec "Downloaded"
                r'Progress:\s*(\d+(?:\.\d+)?)%',  # Format avec "Progress:"
            ]
            
            # Simulation de progression basée sur le temps (fallback)
            start_time = time.time()
            estimated_duration = 30  # Estimation de 30 secondes par défaut
            
            while proc.poll() is None:
                try:
                    # Essayer de lire une ligne avec timeout
                    line = output_queue.get(timeout=0.5)
                    
                    # Chercher un pourcentage dans la ligne
                    progress_found = False
                    for pattern in progress_patterns:
                        match = re.search(pattern, line)
                        if match:
                            try:
                                # Prendre le premier groupe qui contient un pourcentage
                                if len(match.groups()) > 1:
                                    progress = float(match.group(2))  # Deuxième groupe pour les formats avec ratio
                                else:
                                    progress = float(match.group(1))  # Premier groupe pour les autres
                                
                                if 0 <= progress <= 100:
                                    last_progress = progress
                                    self._update_progress(prog=progress, status='📥 Téléchargement')
                                    progress_found = True
                                    break
                            except (ValueError, IndexError):
                                continue
                    
                    # Analyser d'autres indicateurs de statut
                    if not progress_found:
                        line_lower = line.lower()
                        if any(word in line_lower for word in ['downloading', 'téléchargement', 'download']):
                            self._update_progress(status='📥 Téléchargement')
                        elif any(word in line_lower for word in ['converting', 'conversion']):
                            self._update_progress(status='🔄 Conversion')
                        elif any(word in line_lower for word in ['searching', 'recherche']):
                            self._update_progress(status='🔍 Recherche')
                
                except queue.Empty:
                    # Pas de nouvelle ligne, utiliser progression estimée
                    elapsed = time.time() - start_time
                    estimated_progress = min(95, (elapsed / estimated_duration) * 100)
                    if estimated_progress > last_progress:
                        last_progress = estimated_progress
                        self._update_progress(prog=last_progress, status='📥 Téléchargement')
                
                time.sleep(0.1)
            
            # Attendre la fin du processus
            rc = proc.wait()
            
            # Marquer comme terminé
            success = rc == 0
            final_progress = 100 if success else last_progress
            status = '✅ Succès' if success else '❌ Échec'
            
            self._update_progress(prog=final_progress, status=status)
            return success
            
        except Exception as e:
            self._update_progress(prog=0, status=f'❌ Erreur: {str(e)[:20]}')
            return False

    def download_item(self, artist: str, album: str, track_id: str, url_type: str) -> bool:
        artist_dir = self.music_directory / artist
        artist_dir.mkdir(exist_ok=True)
        album_dir = artist_dir / album
        album_dir.mkdir(exist_ok=True)

        # Vérifier si le fichier existe déjà
        if self._file_exists(artist, album, track_id):
            try:
                track_info = self.sp.track(track_id)
                title = sanitize_filename(track_info['name'])
                self._update_progress(track=f'{artist} - {title}', prog=100, status='⏭️ Déjà téléchargé')
                time.sleep(0.3)  # Délai pour voir le message
                with self._lock:
                    self.progress.completed_items += 1
                    self.progress.skipped_items += 1
                return True
            except Exception as e:
                print(f'Erreur lors de la récupération des infos de la piste: {e}')

        # Get track title pour le téléchargement
        try:
            title_raw = self.sp.track(track_id)['name']
        except:
            title_raw = track_id
        title = sanitize_filename(title_raw)

        # Download
        url = f'https://open.spotify.com/track/{track_id}'
        ok = self._download_spotdl(url, album_dir, f'{artist} - {title}')
        
        with self._lock:
            self.progress.completed_items += 1
            if not ok: 
                self.progress.failed_items.append(f'{artist} - {title}')
        
        time.sleep(0.5)  # Pause pour voir le résultat final
        return ok

    def process_urls_file(self, filepath: Optional[str]=None):
        path = Path(filepath) if filepath else self.script_directory/'urls.txt'
        if not path.exists(): 
            print(f'❌ {path} introuvable')
            return
            
        urls = [l.strip() for l in path.read_text(encoding='utf-8').splitlines() if l.strip()]
        all_items=[]
        
        print("🔍 Analyse des URLs...")
        for u in urls:
            print(f'   Analyse: {u}')
            all_items += self.parse_spotify_item(u)
            
        # Remove duplicates
        seen=set(); items=[]
        for a,alb,i,t in all_items:
            if (a,alb,i) not in seen:
                seen.add((a,alb,i)); items.append((a,alb,i,t))
                
        print(f"📊 {len(items)} pistes uniques trouvées")
        
        self.progress.total_items = len(items)
        thread = self._start_progress()
        
        try:
            for a,alb,i,t in items:
                self.download_item(a,alb,i,t)
        finally:
            self._stop_progress()
            
        # Summary
        successful = self.progress.completed_items - len(self.progress.failed_items)
        print(f"\n📈 RÉSUMÉ:")
        print(f"✅ {successful}/{self.progress.total_items} téléchargements réussis")
        print(f"⏭️ {self.progress.skipped_items} fichiers déjà présents")
        if self.progress.failed_items:
            print(f'❌ {len(self.progress.failed_items)} échecs:')
            for f in self.progress.failed_items: 
                print(f'   - {f}')

if __name__=='__main__':
    try:
        SpotifyDownloader().process_urls_file()
    except Exception as e:
        print(f'❌ Erreur fatale: {e}')
