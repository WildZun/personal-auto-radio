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


@dataclass
class DownloadProgress:
    """Classe pour suivre la progression du téléchargement"""
    current_item: str = ""
    total_items: int = 0
    completed_items: int = 0
    current_track: str = ""
    failed_items: List[str] = None
    # Progression du titre en cours
    current_track_progress: float = 0.0
    current_track_status: str = ""
    
    def __post_init__(self):
        if self.failed_items is None:
            self.failed_items = []
    
    def get_progress_percentage(self) -> float:
        if self.total_items == 0:
            return 0
        return (self.completed_items / self.total_items) * 100
    
    def get_global_progress_bar(self, width: int = 40) -> str:
        """Génère une barre de progression globale"""
        filled_width = int(width * self.completed_items / max(self.total_items, 1))
        bar = '█' * filled_width + '░' * (width - filled_width)
        return f"[{bar}] {self.completed_items}/{self.total_items} ({self.get_progress_percentage():.1f}%)"
    
    def get_track_progress_bar(self, width: int = 30) -> str:
        """Génère une barre de progression pour le titre en cours"""
        filled_width = int(width * self.current_track_progress / 100)
        bar = '█' * filled_width + '░' * (width - filled_width)
        return f"[{bar}] {self.current_track_progress:.1f}%"


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
        self._progress_lock = threading.Lock()
    
    def _init_spotify_client(self) -> spotipy.Spotify:
        """Initialise le client Spotify"""
        client_id = os.environ.get('SPOTIFY_CLIENT_ID')
        client_secret = os.environ.get('SPOTIFY_CLIENT_SECRET')
        
        if not client_id or not client_secret:
            raise ValueError("Les variables d'environnement SPOTIFY_CLIENT_ID et SPOTIFY_CLIENT_SECRET sont requises")
        
        auth_manager = SpotifyClientCredentials(client_id=client_id, client_secret=client_secret)
        return spotipy.Spotify(auth_manager=auth_manager)
    
    def _display_progress(self):
        """Affiche la progression en temps réel avec double affichage"""
        lines_printed = 0
        
        while not self._stop_progress_display:
            with self._progress_lock:
                # Effacer les lignes précédentes
                if lines_printed > 0:
                    for _ in range(lines_printed):
                        print("\033[1A\033[K", end="")  # Remonter et effacer la ligne
                
                lines_printed = 0
                
                # Affichage global
                global_bar = self.progress.get_global_progress_bar()
                print(f"🌍 Global: {global_bar}")
                lines_printed += 1
                
                # Affichage du titre en cours
                if self.progress.current_track:
                    track_bar = self.progress.get_track_progress_bar()
                    track_name = self.progress.current_track[:50] + "..." if len(self.progress.current_track) > 50 else self.progress.current_track
                    print(f"🎵 Titre:  {track_bar} {track_name}")
                    lines_printed += 1
                
                # Affichage du statut
                if self.progress.current_track_status:
                    print(f"📊 Status: {self.progress.current_track_status}")
                    lines_printed += 1
                
                # Forcer l'affichage
                sys.stdout.flush()
            
            time.sleep(0.3)  # Réduire la fréquence de mise à jour
    
    def _start_progress_display(self):
        """Démarre l'affichage de la progression dans un thread séparé"""
        self._stop_progress_display = False
        progress_thread = threading.Thread(target=self._display_progress, daemon=True)
        progress_thread.start()
        return progress_thread
    
    def _stop_progress_display_func(self):
        """Arrête l'affichage de la progression"""
        self._stop_progress_display = True
        time.sleep(0.5)  # Attendre que le thread se termine
        print("\n")  # Nouvelle ligne après la barre de progression
    
    def _update_progress(self, track_name: str = None, progress: float = None, status: str = None):
        """Met à jour la progression de manière thread-safe"""
        with self._progress_lock:
            if track_name is not None:
                self.progress.current_track = track_name
            if progress is not None:
                self.progress.current_track_progress = progress
            if status is not None:
                self.progress.current_track_status = status
    
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
        """Récupère les informations d'une playlist avec pagination complète"""
        try:
            playlist = self.sp.playlist(playlist_id)
            playlist_name = playlist['name']
            total_tracks = playlist['tracks']['total']
            
            print(f"📋 Récupération de la playlist '{playlist_name}' ({total_tracks} titres)...")
            
            items = []
            offset = 0
            limit = 100  # Maximum autorisé par Spotify
            
            while offset < total_tracks:
                # Récupérer les tracks par batch de 100
                tracks_batch = self.sp.playlist_tracks(
                    playlist_id, 
                    offset=offset, 
                    limit=limit,
                    fields='items(track(id,name,artists,album)),total'
                )
                
                print(f"📥 Récupération des titres {offset + 1} à {min(offset + limit, total_tracks)}...")
                
                for track_item in tracks_batch['items']:
                    track = track_item.get('track')
                    if track and track.get('id') and track.get('artists'):
                        artist = self._sanitize_filename(track['artists'][0]['name'])
                        album_name = self._sanitize_filename(track['album']['name'])
                        track_id = track['id']
                        items.append((artist, album_name, track_id, 'playlist'))
                
                offset += limit
                
                # Petite pause pour éviter de surcharger l'API
                time.sleep(0.1)
            
            print(f"✅ {len(items)} titres récupérés depuis la playlist '{playlist_name}'")
            return items
            
        except Exception as e:
            print(f"❌ Erreur lors de la récupération de la playlist {playlist_id}: {e}")
            return []
    
    def _get_album_tracks_info(self, album_id: str) -> List[Tuple[str, str, str, str]]:
        """Récupère tous les tracks d'un album avec pagination"""
        try:
            album = self.sp.album(album_id)
            artist = album['artists'][0]['name']
            album_name = album['name']
            total_tracks = album['tracks']['total']
            
            print(f"💿 Récupération de l'album '{album_name}' par {artist} ({total_tracks} titres)...")
            
            items = []
            offset = 0
            limit = 50  # Maximum pour les tracks d'album
            
            while offset < total_tracks:
                # Récupérer les tracks par batch
                tracks_batch = self.sp.album_tracks(
                    album_id,
                    offset=offset,
                    limit=limit
                )
                
                for track in tracks_batch['items']:
                    if track.get('id'):
                        items.append((
                            self._sanitize_filename(artist),
                            self._sanitize_filename(album_name),
                            track['id'],
                            'track'
                        ))
                
                offset += limit
                
                # Petite pause pour éviter de surcharger l'API
                time.sleep(0.1)
            
            print(f"✅ {len(items)} titres récupérés depuis l'album '{album_name}'")
            return items
            
        except Exception as e:
            print(f"❌ Erreur lors de la récupération de l'album {album_id}: {e}")
            return []
    
    def parse_spotify_item(self, url: str) -> List[Tuple[str, str, str, str]]:
        """Parse une URL Spotify et retourne les informations des éléments"""
        try:
            url_type, item_id = self._extract_spotify_info(url)
            
            if url_type == "album":
                return self._get_album_tracks_info(item_id)
            elif url_type == "playlist":
                return self._get_playlist_info(item_id)
            
        except Exception as e:
            print(f"❌ Erreur lors du parsing de l'URL {url}: {e}")
            return []
    
    def _run_spotdl_command(self, command: List[str], cwd: Path, item_name: str) -> bool:
        """Exécute une commande spotdl de manière sécurisée avec suivi détaillé"""
        try:
            # Initialiser la progression du titre
            self._update_progress(
                track_name=item_name,
                progress=0.0,
                status="🔄 Démarrage..."
            )
            
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
            output_lines = []
            error_lines = []
            
            while True:
                output = process.stdout.readline()
                if output == '' and process.poll() is not None:
                    break
                    
                if output:
                    output_clean = output.strip()
                    output_lines.append(output_clean)
                    
                    # Analyser la sortie de spotdl pour extraire la progression
                    if "Searching" in output_clean or "searching" in output_clean.lower():
                        self._update_progress(progress=10.0, status="🔍 Recherche...")
                    elif "Found" in output_clean or "found" in output_clean.lower():
                        self._update_progress(progress=20.0, status="✅ Trouvé")
                        # Extraire le nom du track si disponible
                        if ":" in output_clean:
                            track_info = output_clean.split(":", 1)[-1].strip()
                            if track_info and len(track_info) > 3:
                                clean_track = track_info[:60] + "..." if len(track_info) > 60 else track_info
                                self._update_progress(track_name=clean_track)
                    elif "Downloading" in output_clean or "downloading" in output_clean.lower():
                        self._update_progress(progress=40.0, status="📥 Téléchargement...")
                    elif "Converting" in output_clean or "converting" in output_clean.lower():
                        self._update_progress(progress=70.0, status="🔄 Conversion...")
                    elif "Applying" in output_clean or "metadata" in output_clean.lower():
                        self._update_progress(progress=85.0, status="🏷️ Métadonnées...")
                    elif "Downloaded" in output_clean or "downloaded" in output_clean.lower():
                        self._update_progress(progress=100.0, status="✅ Terminé")
                    elif "%" in output_clean:
                        # Essayer d'extraire un pourcentage si disponible
                        try:
                            percent_match = re.search(r'(\d+(?:\.\d+)?)%', output_clean)
                            if percent_match:
                                percent = float(percent_match.group(1))
                                # Ajuster la progression en fonction de l'étape actuelle
                                adjusted_progress = min(95.0, max(self.progress.current_track_progress, 30.0 + percent * 0.6))
                                self._update_progress(progress=adjusted_progress)
                        except:
                            pass
                    elif "error" in output_clean.lower() or "failed" in output_clean.lower():
                        self._update_progress(progress=0.0, status="❌ Erreur détectée")
            
            # Lire les erreurs
            stderr_output = process.stderr.read()
            if stderr_output:
                error_lines.append(stderr_output.strip())
            
            # Attendre la fin du processus
            return_code = process.wait()
            
            if return_code == 0:
                self._update_progress(progress=100.0, status="✅ Succès")
                time.sleep(0.5)  # Laisser le temps de voir le succès
                return True
            else:
                error_msg = "Erreur inconnue"
                if error_lines:
                    error_msg = error_lines[0][:60]
                elif output_lines:
                    # Chercher des indices d'erreur dans les dernières lignes
                    for line in reversed(output_lines[-5:]):
                        if "error" in line.lower() or "failed" in line.lower():
                            error_msg = line[:60]
                            break
                
                self._update_progress(progress=0.0, status=f"❌ {error_msg}")
                time.sleep(1)  # Laisser le temps de voir l'erreur
                return False
                
        except Exception as e:
            self._update_progress(progress=0.0, status=f"❌ Exception: {str(e)[:40]}")
            time.sleep(1)
            return False
    
    def download_item(self, artist: str, album_name: str, item_id: str, url_type: str) -> bool:
        """Télécharge un élément (album ou track) avec métadonnées complètes"""
        # Créer les dossiers
        artist_folder = self.music_directory / artist
        artist_folder.mkdir(exist_ok=True)
        
        album_folder = artist_folder / album_name
        album_folder.mkdir(exist_ok=True)
        
        # Construire la commande spotdl avec options pour métadonnées
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
        
        # Nom d'affichage pour la progression
        display_name = f"{artist} - {album_name}"
        success = self._run_spotdl_command(command, album_folder, display_name)
        
        # Mettre à jour les compteurs globaux
        with self._progress_lock:
            self.progress.completed_items += 1
            if not success:
                self.progress.failed_items.append(display_name)
        
        # Petite pause entre les téléchargements
        time.sleep(0.2)
        
        return success
    
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
                print(f"🔍 Analyse de l'URL: {url}")
                items = self.parse_spotify_item(url)
                all_items.extend(items)
            
            print(f"📊 Total des titres trouvés: {len(all_items)}")
            
            # Éviter les doublons de tracks
            seen_tracks = set()
            unique_items = []
            
            for artist, album_name, item_id, url_type in all_items:
                track_key = (artist, album_name, item_id)
                if track_key not in seen_tracks:
                    seen_tracks.add(track_key)
                    unique_items.append((artist, album_name, item_id, url_type))
            
            print(f"📊 Titres uniques à télécharger: {len(unique_items)}")
            
            # Initialiser la progression
            self.progress.total_items = len(unique_items)
            self.progress.completed_items = 0
            
            # Démarrer l'affichage de progression
            progress_thread = self._start_progress_display()
            
            try:
                # Traiter chaque item
                for artist, album_name, item_id, url_type in unique_items:
                    success = self.download_item(artist, album_name, item_id, url_type)
                    if not success:
                        print(f"\n⚠️ Échec du téléchargement pour {artist} - {album_name}")
            finally:
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
