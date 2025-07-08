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


@dataclass
class DownloadProgress:
    """Classe pour suivre la progression du téléchargement"""
    current_item: str = ""
    total_items: int = 0
    completed_items: int = 0
    current_track: str = ""
    failed_items: List[str] = None
    
    def __post_init__(self):
        if self.failed_items is None:
            self.failed_items = []
    
    def get_progress_percentage(self) -> float:
        if self.total_items == 0:
            return 0
        return (self.completed_items / self.total_items) * 100
    
    def get_progress_bar(self, width: int = 40) -> str:
        """Génère une barre de progression visuelle"""
        filled_width = int(width * self.completed_items / max(self.total_items, 1))
        bar = '█' * filled_width + '░' * (width - filled_width)
        return f"[{bar}] {self.completed_items}/{self.total_items} ({self.get_progress_percentage():.1f}%)"


class SpotifyDownloader:
    """Classe pour télécharger de la musique depuis Spotify"""
    
    def __init__(self):
        load_dotenv()
        self.script_directory = Path(__file__).parent
        self.music_directory = self.script_directory / "Music"
        self.music_directory.mkdir(exist_ok=True)
        self.sp = self._init_spotify_client()
        self.progress = DownloadProgress()
        self._stop_progress_display = False
    
    def _init_spotify_client(self) -> spotipy.Spotify:
        """Initialise le client Spotify"""
        client_id = os.environ.get('SPOTIFY_CLIENT_ID')
        client_secret = os.environ.get('SPOTIFY_CLIENT_SECRET')
        
        if not client_id or not client_secret:
            raise ValueError("Les variables d'environnement SPOTIFY_CLIENT_ID et SPOTIFY_CLIENT_SECRET sont requises")
        
        auth_manager = SpotifyClientCredentials(client_id=client_id, client_secret=client_secret)
        return spotipy.Spotify(auth_manager=auth_manager)
    
    def _display_progress(self):
        """Affiche la progression en temps réel"""
        while not self._stop_progress_display:
            # Effacer la ligne précédente et afficher la progression
            print(f"\r🎵 {self.progress.get_progress_bar()}", end="", flush=True)
            if self.progress.current_item:
                print(f"\n📥 {self.progress.current_item}", end="", flush=True)
            if self.progress.current_track:
                print(f"\n🎧 {self.progress.current_track}", end="", flush=True)
            
            time.sleep(0.5)
            
            # Effacer les lignes pour la prochaine mise à jour
            if self.progress.current_item or self.progress.current_track:
                print("\r" + " " * 100 + "\r", end="", flush=True)
                if self.progress.current_track:
                    print("\r" + " " * 100 + "\r", end="", flush=True)
    
    def _start_progress_display(self):
        """Démarre l'affichage de la progression dans un thread séparé"""
        self._stop_progress_display = False
        progress_thread = threading.Thread(target=self._display_progress, daemon=True)
        progress_thread.start()
        return progress_thread
    
    def _stop_progress_display_func(self):
        """Arrête l'affichage de la progression"""
        self._stop_progress_display = True
        print("\n")  # Nouvelle ligne après la barre de progression
    
    def _sanitize_filename(self, filename: str) -> str:
        """Nettoie le nom de fichier pour éviter les caractères invalides"""
        # Remplace les caractères problématiques par des underscores
        return re.sub(r'[<>:"/\\|?*]', '_', filename).strip()
    
    def _extract_spotify_info(self, url: str) -> Tuple[str, str]:
        """Extrait le type et l'ID depuis une URL Spotify"""
        if 'spotify.com' not in url:
            raise ValueError("URL Spotify invalide")
        
        # Pattern plus robuste pour extraire le type et l'ID
        pattern = r'spotify\.com/([^/]+)/([^/?]+)'
        match = re.search(pattern, url)
        
        if not match:
            raise ValueError("Impossible d'extraire les informations de l'URL")
        
        url_type, item_id = match.groups()
        
        if url_type not in ['album', 'playlist']:
            raise ValueError("Le type doit être 'album' ou 'playlist'")
        
        return url_type, item_id
    
    def _get_album_info(self, album_id: str) -> List[Tuple[str, str, str, str]]:
        """Récupère les informations d'un album"""
        try:
            album = self.sp.album(album_id)
            artist = album['artists'][0]['name']
            album_name = album['name']
            return [(self._sanitize_filename(artist), self._sanitize_filename(album_name), album_id, 'album')]
        except Exception as e:
            print(f"Erreur lors de la récupération de l'album {album_id}: {e}")
            return []
    
    def _get_playlist_info(self, playlist_id: str) -> List[Tuple[str, str, str, str]]:
        """Récupère les informations d'une playlist"""
        try:
            playlist = self.sp.playlist(playlist_id)
            items = []
            
            for track_item in playlist['tracks']['items']:
                track = track_item.get('track')
                if track and track['artists']:
                    artist = self._sanitize_filename(track['artists'][0]['name'])
                    album_name = self._sanitize_filename(track['album']['name'])
                    track_id = track['id']
                    items.append((artist, album_name, track_id, 'playlist'))
            
            return items
        except Exception as e:
            print(f"Erreur lors de la récupération de la playlist {playlist_id}: {e}")
            return []
    
    def parse_spotify_item(self, url: str) -> List[Tuple[str, str, str, str]]:
        """Parse une URL Spotify et retourne les informations des éléments"""
        try:
            url_type, item_id = self._extract_spotify_info(url)
            
            if url_type == "album":
                return self._get_album_info(item_id)
            elif url_type == "playlist":
                return self._get_playlist_info(item_id)
            
        except Exception as e:
            print(f"Erreur lors du parsing de l'URL {url}: {e}")
            return []
    
    def _run_spotdl_command(self, command: List[str], cwd: Path, item_name: str) -> bool:
        """Exécute une commande spotdl de manière sécurisée avec métadonnées complètes"""
        try:
            # Mettre à jour le statut
            self.progress.current_item = f"Téléchargement: {item_name}"
            
            # Créer le processus
            process = subprocess.Popen(
                command,
                cwd=cwd,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                bufsize=1,
                universal_newlines=True
            )
            
            # Suivre la sortie en temps réel
            while True:
                output = process.stdout.readline()
                if output == '' and process.poll() is not None:
                    break
                if output:
                    # Extraire les informations de progression de spotdl
                    output_clean = output.strip()
                    if "Downloading" in output_clean:
                        # Extraire le nom du track en cours
                        track_info = output_clean.split("Downloading")[-1].strip()
                        self.progress.current_track = track_info[:60] + "..." if len(track_info) > 60 else track_info
                    elif "Downloaded" in output_clean:
                        track_info = output_clean.split("Downloaded")[-1].strip()
                        self.progress.current_track = f"✅ {track_info[:50]}..." if len(track_info) > 50 else f"✅ {track_info}"
            
            # Attendre la fin du processus
            stderr_output = process.stderr.read()
            return_code = process.wait()
            
            if return_code == 0:
                self.progress.current_track = f"✅ Terminé: {item_name}"
                time.sleep(1)  # Laisser le temps de voir le message
                return True
            else:
                self.progress.current_track = f"❌ Échec: {item_name}"
                if stderr_output:
                    print(f"\n❌ Erreur: {stderr_output}")
                return False
                
        except Exception as e:
            self.progress.current_track = f"❌ Erreur: {str(e)}"
            return False
    
    def download_item(self, artist: str, album_name: str, item_id: str, url_type: str) -> bool:
        """Télécharge un élément (album ou track) avec métadonnées complètes"""
        # Créer les dossiers
        artist_folder = self.music_directory / artist
        artist_folder.mkdir(exist_ok=True)
        
        album_folder = artist_folder / album_name
        album_folder.mkdir(exist_ok=True)
        
        # Construire la commande spotdl avec options pour métadonnées
        if url_type == "album":
            spotify_url = f"https://open.spotify.com/album/{item_id}"
        else:
            spotify_url = f"https://open.spotify.com/track/{item_id}"
        
        # Commande avec options pour métadonnées complètes
        command = [
            "spotdl", 
            "download",
            spotify_url,
            "--format", "mp3",  # Format audio
            "--bitrate", "320k",  # Qualité audio (avec 'k')
            "--overwrite", "skip"  # Ne pas re-télécharger les fichiers existants
        ]
        
        item_name = f"{artist} - {album_name}"
        success = self._run_spotdl_command(command, album_folder, item_name)
        
        # Mettre à jour les compteurs
        self.progress.completed_items += 1
        if not success:
            self.progress.failed_items.append(item_name)
        
        return success # spotdl avec options pour métadonnées
        if url_type == "album":
            spotify_url = f"https://open.spotify.com/album/{item_id}"
        else:
            spotify_url = f"https://open.spotify.com/track/{item_id}"
        
        # Commande avec options pour métadonnées complètes
        command = [
            "spotdl", 
            "download",
            spotify_url,
            "--format", "mp3",  # Format audio
            "--bitrate", "320",  # Qualité audio
            "--embed-metadata",  # Intégrer les métadonnées
            "--generate-lrc",  # Générer les paroles si disponibles
            "--overwrite", "skip"  # Ne pas re-télécharger les fichiers existants
        ]
        
        print(f"📥 Téléchargement de {artist} - {album_name}...")
        return self._run_spotdl_command(command, album_folder)
    
    def process_urls_file(self, file_path: Optional[str] = None) -> None:
        """Traite un fichier contenant des URLs Spotify"""
        if file_path is None:
            file_path = self.script_directory / 'urls.txt'
        else:
            file_path = Path(file_path)
        
        if not file_path.exists():
            print(f"❌ Le fichier {file_path} n'existe pas")
            return
        
        try:
            with open(file_path, 'r', encoding='utf-8') as file:
                urls = [line.strip() for line in file if line.strip()]
            
            print(f"📋 Traitement de {len(urls)} URL(s)...")
            
            # Compter le nombre total d'items à télécharger
            all_items = []
            for url in urls:
                items = self.parse_spotify_item(url)
                all_items.extend(items)
            
            # Éviter les doublons d'albums
            processed_albums = set()
            unique_items = []
            
            for artist, album_name, item_id, url_type in all_items:
                if url_type == 'playlist':
                    album_key = (artist, album_name)
                    if album_key in processed_albums:
                        continue
                    processed_albums.add(album_key)
                unique_items.append((artist, album_name, item_id, url_type))
            
            # Initialiser la progression
            self.progress.total_items = len(unique_items)
            self.progress.completed_items = 0
            
            # Démarrer l'affichage de progression
            progress_thread = self._start_progress_display()
            
            # Traiter chaque item
            for artist, album_name, item_id, url_type in unique_items:
                success = self.download_item(artist, album_name, item_id, url_type)
                if not success:
                    print(f"\n⚠️ Échec du téléchargement pour {artist} - {album_name}")
            
            # Arrêter l'affichage de progression
            self._stop_progress_display_func()
            
            # Résumé final
            print(f"\n✅ Téléchargement terminé !")
            print(f"📊 {self.progress.completed_items - len(self.progress.failed_items)}/{self.progress.total_items} réussis")
            
            if self.progress.failed_items:
                print(f"❌ {len(self.progress.failed_items)} échecs:")
                for failed_item in self.progress.failed_items:
                    print(f"   - {failed_item}")
            
        except Exception as e:
            self._stop_progress_display_func()
            print(f"❌ Erreur lors du traitement du fichier: {e}")


def main():
    """Fonction principale"""
    try:
        downloader = SpotifyDownloader()
        downloader.process_urls_file()
    except Exception as e:
        print(f"❌ Erreur fatale: {e}")


if __name__ == "__main__":
    main()
