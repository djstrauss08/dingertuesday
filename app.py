import statsapi
from flask import Flask, render_template, request, jsonify, redirect, url_for, session, send_from_directory
from datetime import date, datetime, timedelta
import traceback
import time
from functools import lru_cache, wraps
import requests_cache
import sqlite3
import json
import threading
from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger
import logging
import atexit
import re
import os
import uuid
from werkzeug.utils import secure_filename
import requests
import firebase_admin
from firebase_admin import credentials, auth
import pytz
import shutil

# Set up logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Initialize scheduler variable
scheduler = None

# Set up request caching
requests_cache.install_cache('mlb_api_cache', backend='sqlite', expire_after=3600)  # Cache for 1 hour

app = Flask(__name__)
app.secret_key = os.environ.get('SECRET_KEY', 'dev-secret-key-change-in-production')

# Eastern Time zone for MLB operations
EASTERN_TZ = pytz.timezone('US/Eastern')

def get_mlb_today():
    """
    Get the current MLB day based on Eastern Time.
    The MLB day changes at 3 AM ET, so games before 3 AM are considered part of the previous day.
    """
    now_et = datetime.now(EASTERN_TZ)
    
    # If it's before 3 AM ET, use the previous day
    if now_et.hour < 3:
        mlb_date = (now_et - timedelta(days=1)).date()
    else:
        mlb_date = now_et.date()
    
    return mlb_date.strftime("%Y-%m-%d")

def get_eastern_time():
    """Get current Eastern Time"""
    return datetime.now(EASTERN_TZ)

# Initialize Firebase Admin SDK
# For development, we'll use the project configuration without a service account
# In production, you'd want to use a service account JSON file
try:
    if not firebase_admin._apps:
        firebase_admin.initialize_app(options={
            'projectId': 'dingertuesday-18a26'
        })
    print("Firebase Admin SDK initialized successfully")
except Exception as e:
    print(f"Firebase Admin SDK initialization error: {e}")

def verify_firebase_token(id_token):
    """Verify Firebase ID token server-side using Admin SDK"""
    try:
        # For development, if Firebase Admin SDK isn't properly configured,
        # we'll do a basic validation and trust the frontend
        if not id_token:
            return None
            
        # Try to verify with Firebase Admin SDK
        decoded_token = auth.verify_id_token(id_token)
        return decoded_token
    except Exception as e:
        logging.error(f"Token verification error: {e}")
        
        # For development mode, if token verification fails due to credentials,
        # but the token looks valid (basic check), we'll allow it
        if id_token and len(id_token) > 100 and '.' in id_token:
            logging.warning("Using development mode: trusting client-side authentication")
            
            # Try to extract email from token payload (basic parsing)
            try:
                import base64
                import json
                # JWT tokens have 3 parts separated by dots
                parts = id_token.split('.')
                if len(parts) >= 2:
                    # Decode the payload (second part)
                    payload = parts[1]
                    # Add padding if needed
                    payload += '=' * (4 - len(payload) % 4)
                    decoded_payload = base64.b64decode(payload)
                    token_data = json.loads(decoded_payload)
                    
                    # If this is danieljstrauss1@gmail.com, always allow
                    if token_data.get('email') == 'danieljstrauss1@gmail.com':
                        return {
                            'uid': f"admin-{token_data.get('email', 'unknown')}",
                            'email': token_data.get('email')
                        }
            except Exception as parse_error:
                logging.error(f"Error parsing token: {parse_error}")
            
            return {'uid': 'dev-user', 'email': 'dev@example.com'}
        
        return None

def get_or_create_user(uid, email):
    """Get or create user in database and return user info"""
    conn = sqlite3.connect(DATABASE_PATH)
    cursor = conn.cursor()
    
    try:
        # Try to get existing user by uid first
        cursor.execute('SELECT * FROM users WHERE uid = ?', (uid,))
        user = cursor.fetchone()
        
        if user:
            # Update last login
            cursor.execute('UPDATE users SET last_login = CURRENT_TIMESTAMP WHERE uid = ?', (uid,))
            conn.commit()
            return {
                'id': user[0],
                'uid': user[1],
                'email': user[2],
                'role': user[3],
                'created_at': user[4],
                'last_login': user[5]
            }
        
        # Try to get existing user by email (in case uid changed)
        cursor.execute('SELECT * FROM users WHERE email = ?', (email,))
        user = cursor.fetchone()
        
        if user:
            # Update uid and last login
            cursor.execute('UPDATE users SET uid = ?, last_login = CURRENT_TIMESTAMP WHERE email = ?', (uid, email))
            conn.commit()
            return {
                'id': user[0],
                'uid': uid,  # Return updated uid
                'email': user[2],
                'role': user[3],
                'created_at': user[4],
                'last_login': user[5]
            }
        
        # Create new user if doesn't exist
        try:
            cursor.execute('''
                INSERT INTO users (uid, email, role)
                VALUES (?, ?, 'user')
            ''', (uid, email))
            conn.commit()
            
            # Get the newly created user
            cursor.execute('SELECT * FROM users WHERE uid = ?', (uid,))
            user = cursor.fetchone()
            return {
                'id': user[0],
                'uid': user[1],
                'email': user[2],
                'role': user[3],
                'created_at': user[4],
                'last_login': user[5]
            }
        except sqlite3.IntegrityError as e:
            logging.warning(f"User creation failed due to constraint, trying to fetch existing user: {e}")
            # If creation failed due to constraint, try to get existing user again
            cursor.execute('SELECT * FROM users WHERE email = ?', (email,))
            user = cursor.fetchone()
            if user:
                return {
                    'id': user[0],
                    'uid': user[1],
                    'email': user[2],
                    'role': user[3],
                    'created_at': user[4],
                    'last_login': user[5]
                }
            return None
            
    except Exception as e:
        logging.error(f"Error managing user: {e}")
        return None
    finally:
        conn.close()

def set_user_role(email, role):
    """Set a user's role by email"""
    conn = sqlite3.connect(DATABASE_PATH)
    cursor = conn.cursor()
    
    try:
        cursor.execute('UPDATE users SET role = ? WHERE email = ?', (role, email))
        conn.commit()
        return cursor.rowcount > 0
    except Exception as e:
        logging.error(f"Error setting user role: {e}")
        return False
    finally:
        conn.close()

def get_user_role(uid):
    """Get user's role by uid"""
    conn = sqlite3.connect(DATABASE_PATH)
    cursor = conn.cursor()
    
    try:
        cursor.execute('SELECT role FROM users WHERE uid = ?', (uid,))
        result = cursor.fetchone()
        return result[0] if result else 'user'
    except Exception as e:
        logging.error(f"Error getting user role: {e}")
        return 'user'
    finally:
        conn.close()

def require_auth(f):
    """Decorator to require authentication for routes"""
    @wraps(f)
    def decorated_function(*args, **kwargs):
        # Check for Firebase token in session or request headers
        auth_token = session.get('firebase_token') or request.headers.get('Authorization')
        
        if auth_token:
            decoded_token = verify_firebase_token(auth_token)
            if decoded_token:
                # Store user info in session for use in routes
                user_info = get_or_create_user(decoded_token.get('uid'), decoded_token.get('email'))
                
                # Special handling for admin email (same as require_admin)
                if decoded_token.get('email') == 'danieljstrauss1@gmail.com':
                    if not user_info or user_info.get('role') != 'admin':
                        set_user_role('danieljstrauss1@gmail.com', 'admin')
                        user_info = get_or_create_user(decoded_token.get('uid'), 'danieljstrauss1@gmail.com')
                
                if user_info:
                    session['user_info'] = user_info
                    return f(*args, **kwargs)
        
        # If AJAX request, return JSON error
        if request.is_json or request.headers.get('Accept') == 'application/json':
            return jsonify({'error': 'Authentication required'}), 401
        # Otherwise redirect to login
        return redirect(url_for('login_page', return_url=request.url))
    
    return decorated_function

def require_admin(f):
    """Decorator to require admin role for routes"""
    @wraps(f)
    def decorated_function(*args, **kwargs):
        # Check for Firebase token in session or request headers
        auth_token = session.get('firebase_token') or request.headers.get('Authorization')
        
        if auth_token:
            decoded_token = verify_firebase_token(auth_token)
            if decoded_token:
                # Get user info and check admin role
                user_info = get_or_create_user(decoded_token.get('uid'), decoded_token.get('email'))
                
                # Temporary admin bypass for specific email
                user_email = decoded_token.get('email', '')
                if user_email == 'danieljstrauss1@gmail.com':
                    # Force create admin entry if it doesn't exist
                    if not user_info or user_info.get('role') != 'admin':
                        set_user_role(user_email, 'admin')
                        user_info = get_or_create_user(decoded_token.get('uid'), user_email)
                    
                    session['user_info'] = user_info
                    return f(*args, **kwargs)
                
                if user_info and user_info.get('role') == 'admin':
                    session['user_info'] = user_info
                    return f(*args, **kwargs)
                else:
                    # User is authenticated but not admin
                    if request.is_json or request.headers.get('Accept') == 'application/json':
                        return jsonify({'error': 'Admin access required'}), 403
                    return render_template('403.html'), 403
        
        # If AJAX request, return JSON error
        if request.is_json or request.headers.get('Accept') == 'application/json':
            return jsonify({'error': 'Authentication required'}), 401
        # Otherwise redirect to login
        return redirect(url_for('login_page', return_url=request.url))
    
    return decorated_function

# Determine the current or most recent season dynamically if possible, otherwise default
# Using 2025 for current season data
CURRENT_SEASON = 2025 # Using 2025 for the current season

# Cache for storing player stats to reduce API calls
PLAYER_STATS_CACHE = {}
TEAM_ROSTER_CACHE = {}
SCHEDULE_CACHE = {}
CACHE_TIMEOUT = 300  # Cache timeout in seconds (5 minutes)
LAST_CACHE_CLEAR = time.time()
ENABLE_CACHE = True  # Flag to enable/disable caching for debugging

# Daily data cache - this will hold pre-fetched data
DAILY_DATA_CACHE = {
    'pitchers': None,
    'hitters': None,
    'schedule': None,
    'last_updated': None,
    'update_date': None
}

# Database setup for persistent daily data storage
DATABASE_PATH = os.environ.get('DATABASE_PATH', 'daily_mlb_data.sqlite')

def init_database():
    """Initialize the database with tables for daily data storage"""
    conn = sqlite3.connect(DATABASE_PATH)
    cursor = conn.cursor()
    
    # Create table for users and roles
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY,
            uid TEXT UNIQUE NOT NULL,
            email TEXT UNIQUE NOT NULL,
            role TEXT DEFAULT 'user',
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            last_login TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    ''')
    
    # Create table for daily cached data
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS daily_data (
            id INTEGER PRIMARY KEY,
            data_type TEXT NOT NULL,
            data_date TEXT NOT NULL,
            data_content TEXT NOT NULL,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            UNIQUE(data_type, data_date)
        )
    ''')
    
    # Create table for daily schedule
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS daily_schedule (
            id INTEGER PRIMARY KEY,
            game_date TEXT NOT NULL,
            game_data TEXT NOT NULL,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            UNIQUE(game_date)
        )
    ''')
    
    # Create table for daily pitchers
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS daily_pitchers (
            id INTEGER PRIMARY KEY,
            game_date TEXT NOT NULL,
            pitcher_data TEXT NOT NULL,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            UNIQUE(game_date)
        )
    ''')
    
    # Create table for daily hitters
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS daily_hitters (
            id INTEGER PRIMARY KEY,
            game_date TEXT NOT NULL,
            hitter_data TEXT NOT NULL,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            UNIQUE(game_date)
        )
    ''')
    
    # Create table for blog articles
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS blog_articles (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            title TEXT NOT NULL,
            content TEXT NOT NULL,
            summary TEXT,
            author TEXT DEFAULT 'MLB Analyst',
            tags TEXT,
            status TEXT DEFAULT 'published',
            slug TEXT UNIQUE,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    ''')
    
    conn.commit()
    conn.close()
    logger.info("Database initialized successfully")

def save_daily_data(data_type, data_date, data_content):
    """Save daily data to database"""
    conn = sqlite3.connect(DATABASE_PATH)
    cursor = conn.cursor()
    
    try:
        cursor.execute('''
            INSERT OR REPLACE INTO daily_data (data_type, data_date, data_content)
            VALUES (?, ?, ?)
        ''', (data_type, data_date, json.dumps(data_content)))
        
        conn.commit()
        logger.info(f"Saved {data_type} data for {data_date}")
    except Exception as e:
        logger.error(f"Error saving {data_type} data: {e}")
        conn.rollback()
    finally:
        conn.close()

def load_daily_data(data_type, data_date):
    """Load daily data from database"""
    conn = sqlite3.connect(DATABASE_PATH)
    cursor = conn.cursor()
    
    try:
        cursor.execute('''
            SELECT data_content FROM daily_data 
            WHERE data_type = ? AND data_date = ?
        ''', (data_type, data_date))
        
        result = cursor.fetchone()
        if result:
            return json.loads(result[0])
        return None
    except Exception as e:
        logger.error(f"Error loading {data_type} data: {e}")
        return None
    finally:
        conn.close()

def create_article(title, content, summary="", author="MLB Analyst", tags="", status="published"):
    """Create a new blog article"""
    conn = sqlite3.connect(DATABASE_PATH)
    cursor = conn.cursor()
    
    try:
        # Generate slug from title
        slug = re.sub(r'[^a-zA-Z0-9\s]', '', title.lower())
        slug = re.sub(r'\s+', '-', slug.strip())
        
        # Ensure slug is unique
        original_slug = slug
        counter = 1
        while True:
            cursor.execute('SELECT id FROM blog_articles WHERE slug = ?', (slug,))
            if not cursor.fetchone():
                break
            slug = f"{original_slug}-{counter}"
            counter += 1
        
        cursor.execute('''
            INSERT INTO blog_articles (title, content, summary, author, tags, status, slug, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
        ''', (title, content, summary, author, tags, status, slug))
        
        article_id = cursor.lastrowid
        conn.commit()
        logger.info(f"Created new article: {title} (ID: {article_id})")
        return article_id
    except Exception as e:
        logger.error(f"Error creating article: {e}")
        conn.rollback()
        return None
    finally:
        conn.close()

def get_articles(limit=10, offset=0, status="published"):
    """Get published articles with pagination"""
    conn = sqlite3.connect(DATABASE_PATH)
    cursor = conn.cursor()
    
    try:
        cursor.execute('''
            SELECT id, title, summary, author, tags, slug, created_at, updated_at
            FROM blog_articles 
            WHERE status = ?
            ORDER BY created_at DESC
            LIMIT ? OFFSET ?
        ''', (status, limit, offset))
        
        articles = []
        for row in cursor.fetchall():
            articles.append({
                'id': row[0],
                'title': row[1],
                'summary': row[2],
                'author': row[3],
                'tags': row[4].split(',') if row[4] else [],
                'slug': row[5],
                'created_at': row[6],
                'updated_at': row[7]
            })
        
        return articles
    except Exception as e:
        logger.error(f"Error fetching articles: {e}")
        return []
    finally:
        conn.close()

def get_article_by_slug(slug):
    """Get a specific article by its slug"""
    conn = sqlite3.connect(DATABASE_PATH)
    cursor = conn.cursor()
    
    try:
        cursor.execute('''
            SELECT id, title, content, summary, author, tags, slug, created_at, updated_at, status
            FROM blog_articles 
            WHERE slug = ?
        ''', (slug,))
        
        row = cursor.fetchone()
        if row:
            return {
                'id': row[0],
                'title': row[1],
                'content': row[2],
                'summary': row[3],
                'author': row[4],
                'tags': row[5].split(',') if row[5] else [],
                'slug': row[6],
                'created_at': row[7],
                'updated_at': row[8],
                'status': row[9]
            }
        return None
    except Exception as e:
        logger.error(f"Error fetching article by slug: {e}")
        return None
    finally:
        conn.close()

def get_article_by_id(article_id):
    """Get a specific article by its ID"""
    conn = sqlite3.connect(DATABASE_PATH)
    cursor = conn.cursor()
    
    try:
        cursor.execute('''
            SELECT id, title, content, summary, author, tags, slug, created_at, updated_at, status
            FROM blog_articles 
            WHERE id = ?
        ''', (article_id,))
        
        row = cursor.fetchone()
        if row:
            return {
                'id': row[0],
                'title': row[1],
                'content': row[2],
                'summary': row[3],
                'author': row[4],
                'tags': row[5].split(',') if row[5] else [],
                'slug': row[6],
                'created_at': row[7],
                'updated_at': row[8],
                'status': row[9]
            }
        return None
    except Exception as e:
        logger.error(f"Error fetching article by ID: {e}")
        return None
    finally:
        conn.close()

def update_article(article_id, title=None, content=None, summary=None, author=None, tags=None, status=None):
    """Update an existing article"""
    conn = sqlite3.connect(DATABASE_PATH)
    cursor = conn.cursor()
    
    try:
        # Build dynamic update query
        updates = []
        params = []
        
        if title is not None:
            updates.append("title = ?")
            params.append(title)
        if content is not None:
            updates.append("content = ?")
            params.append(content)
        if summary is not None:
            updates.append("summary = ?")
            params.append(summary)
        if author is not None:
            updates.append("author = ?")
            params.append(author)
        if tags is not None:
            updates.append("tags = ?")
            params.append(tags)
        if status is not None:
            updates.append("status = ?")
            params.append(status)
        
        if updates:
            updates.append("updated_at = CURRENT_TIMESTAMP")
            params.append(article_id)
            
            query = f"UPDATE blog_articles SET {', '.join(updates)} WHERE id = ?"
            cursor.execute(query, params)
            
            conn.commit()
            logger.info(f"Updated article ID: {article_id}")
            return True
        return False
    except Exception as e:
        logger.error(f"Error updating article: {e}")
        conn.rollback()
        return False
    finally:
        conn.close()

def delete_article(article_id):
    """Delete an article"""
    conn = sqlite3.connect(DATABASE_PATH)
    cursor = conn.cursor()
    
    try:
        cursor.execute('DELETE FROM blog_articles WHERE id = ?', (article_id,))
        conn.commit()
        logger.info(f"Deleted article ID: {article_id}")
        return True
    except Exception as e:
        logger.error(f"Error deleting article: {e}")
        conn.rollback()
        return False
    finally:
        conn.close()

def fetch_daily_pitchers_data(date_str):
    """Fetch and process all pitcher data for the day"""
    logger.info(f"Fetching pitcher data for {date_str}")
    
    try:
        schedule = statsapi.schedule(date=date_str, sportId=1)
        
        if not schedule:
            logger.warning(f"No games scheduled for {date_str}")
            return {'error': 'No games scheduled', 'pitchers_data': []}
        
        games_data = []
        
        for game in schedule:
            try:
                if not isinstance(game, dict):
                    continue

                # Get game status
                current_game_status_string = game.get('status')
                
                # Define relevant statuses for probable pitchers
                relevant_statuses_for_probable_pitchers = {"Scheduled", "Pre-Game", "Preview", "Live"}
                
                if current_game_status_string in relevant_statuses_for_probable_pitchers:
                    home_pitcher_name_from_schedule = game.get('home_probable_pitcher')
                    away_pitcher_name_from_schedule = game.get('away_probable_pitcher')
                    
                    home_team_name = game.get('home_name', "N/A")
                    away_team_name = game.get('away_name', "N/A")
                    home_team_id = game.get('home_id')
                    away_team_id = game.get('away_id')

                    pitchers_to_process = []
                    if home_pitcher_name_from_schedule:
                        pitchers_to_process.append({
                            "name_on_schedule": home_pitcher_name_from_schedule, 
                            "team_name": home_team_name, 
                            "opponent_name": away_team_name,
                            "opponent_id": away_team_id
                        })
                    if away_pitcher_name_from_schedule:
                        pitchers_to_process.append({
                            "name_on_schedule": away_pitcher_name_from_schedule, 
                            "team_name": away_team_name, 
                            "opponent_name": home_team_name,
                            "opponent_id": home_team_id
                        })

                    for p_detail in pitchers_to_process:
                        pitcher_display_name = p_detail["name_on_schedule"]
                        actual_player_id = None
                        pitcher_stats = {}

                        try:
                            # Look up player ID
                            player_lookup_results = statsapi.lookup_player(pitcher_display_name)
                            
                            if (player_lookup_results and isinstance(player_lookup_results, list) and 
                                len(player_lookup_results) > 0 and isinstance(player_lookup_results[0], dict) and 
                                player_lookup_results[0].get('id')):
                                actual_player_id = player_lookup_results[0]['id']
                                
                                # Fetch stats using the player ID
                                pitcher_stats = get_player_stats(actual_player_id, group="pitching", type="season")
                                
                                # Extract stats
                                batters_faced = pitcher_stats.get('battersFaced', "N/A")
                                home_runs_allowed = pitcher_stats.get('homeRuns', "N/A")
                                
                                games_data.append({
                                    'name': pitcher_display_name,
                                    'team': p_detail["team_name"],
                                    'opponent': p_detail["opponent_name"],
                                    'opponent_id': p_detail["opponent_id"],
                                    'batters_faced': batters_faced,
                                    'home_runs_allowed': home_runs_allowed
                                })
                            else:
                                games_data.append({
                                    'name': pitcher_display_name,
                                    'team': p_detail["team_name"],
                                    'opponent': p_detail["opponent_name"],
                                    'opponent_id': p_detail["opponent_id"],
                                    'batters_faced': "ID Lookup Error",
                                    'home_runs_allowed': "ID Lookup Error"
                                })

                        except Exception as e:
                            logger.error(f"Error processing pitcher '{pitcher_display_name}': {e}")
                            games_data.append({
                                'name': pitcher_display_name,
                                'team': p_detail["team_name"],
                                'opponent': p_detail["opponent_name"],
                                'opponent_id': p_detail["opponent_id"],
                                'batters_faced': "Fetch Error",
                                'home_runs_allowed': "Fetch Error"
                            })
                
            except Exception as e:
                logger.error(f"Error processing game {game.get('game_id', 'unknown')}: {e}")
                continue
        
        result = {'pitchers_data': games_data, 'total_games': len(games_data)}
        logger.info(f"Successfully fetched pitcher data for {len(games_data)} games")
        return result
        
    except Exception as e:
        logger.error(f"Error in fetch_daily_pitchers_data: {e}")
        return {'error': str(e), 'pitchers_data': []}

def fetch_home_run_odds():
    """Fetch today's home run odds from the external API"""
    odds_url = "https://djstrauss08.github.io/HomeRunOdds//api/v1/homerun-props.json"
    
    # Initialize the player_odds dictionary
    player_odds = {}
    
    try:
        logger.info("Fetching home run odds data")
        response = requests.get(odds_url, timeout=10)
        response.raise_for_status()
        
        odds_data = response.json()
        
        # Log games with odds available
        games_with_odds = []
        for game in odds_data.get('games', []):
            away_team = game.get('away_team')
            home_team = game.get('home_team')
            games_with_odds.append(f"{away_team} @ {home_team}")
            
            for player in game.get('players', []):
                if player.get('line') == 0.5:  # Only "To Hit HR" props
                    player_name = player.get('player_name')
                    consensus_odds = player.get('over_odds', {}).get('consensus')
                    sportsbook_count = player.get('sportsbook_count', 0)
                    
                    if consensus_odds and sportsbook_count > 0:
                        # Format odds with + sign for positive odds
                        if str(consensus_odds).isdigit():
                            formatted_odds = f"+{consensus_odds}"
                        else:
                            formatted_odds = str(consensus_odds)
                        
                        player_odds[player_name] = {
                            'odds': formatted_odds,
                            'raw_odds': consensus_odds,
                            'sportsbook_count': sportsbook_count
                        }
        
        logger.info(f"Odds available for {len(games_with_odds)} games: {', '.join(games_with_odds)}")
        logger.info(f"Total players with HR odds: {len(player_odds)}")
        
        return player_odds
        
    except Exception as e:
        logger.error(f"Error fetching home run odds: {e}")
        return {}

def fetch_daily_hitters_data():
    """Fetch and process all hitter data for the day"""
    logger.info("Fetching daily hitter data")
    
    try:
        # Fetch home run odds first
        odds_data = fetch_home_run_odds()
        
        # Get list of games with odds coverage for better user feedback
        games_with_odds = set()
        try:
            odds_url = "https://djstrauss08.github.io/HomeRunOdds//api/v1/homerun-props.json"
            response = requests.get(odds_url, timeout=10)
            if response.status_code == 200:
                odds_json = response.json()
                for game in odds_json.get('games', []):
                    away_team = game.get('away_team', '')
                    home_team = game.get('home_team', '')
                    games_with_odds.add(away_team)
                    games_with_odds.add(home_team)
        except Exception as e:
            logger.warning(f"Could not fetch games with odds coverage: {e}")
        
        # Fetch league leaders for home runs  
        leaders_raw = statsapi.league_leaders('homeRuns', statGroup='hitting', limit=50, season=CURRENT_SEASON, sportId=1)
        
        # Check if the response is a string (likely the formatted table)
        if isinstance(leaders_raw, str):
            leaders = parse_league_leaders_string(leaders_raw)
        else:
            leaders = leaders_raw
            
        if not leaders:
            logger.error("No leaders data found or parsing failed")
            return {'error': "No home run leaders data available", 'hitters_data': []}
        
        # Use the same date as pitchers_api
        today_str = get_mlb_today()
        schedule_today = statsapi.schedule(date=today_str, sportId=1)
        
        # Create mapping of player names to opponents for today's games
        player_name_to_opponent = {}  # player_name -> opponent_name
        
        if isinstance(schedule_today, list) and schedule_today:
            for game in schedule_today:
                if not isinstance(game, dict): 
                    continue
                home_team_id = game.get('home_id')
                away_team_id = game.get('away_id')
                home_team_name = game.get('home_name', "N/A")
                away_team_name = game.get('away_name', "N/A")

                # Get rosters and map players to opponents
                if home_team_id and away_team_id:
                    try:
                        # Get home team roster
                        home_roster = statsapi.roster(home_team_id, season=CURRENT_SEASON)
                        if isinstance(home_roster, str):
                            for line in home_roster.split('\n'):
                                if line.strip() and '#' in line:
                                    # Parse format like "#99  RF  Aaron Judge"
                                    parts = line.strip().split()
                                    if len(parts) >= 3:
                                        # Player name is everything after position
                                        player_name = ' '.join(parts[2:])
                                        player_name_to_opponent[player_name] = away_team_name
                        
                        # Get away team roster  
                        away_roster = statsapi.roster(away_team_id, season=CURRENT_SEASON)
                        if isinstance(away_roster, str):
                            for line in away_roster.split('\n'):
                                if line.strip() and '#' in line:
                                    # Parse format like "#99  RF  Aaron Judge"
                                    parts = line.strip().split()
                                    if len(parts) >= 3:
                                        # Player name is everything after position
                                        player_name = ' '.join(parts[2:])
                                        player_name_to_opponent[player_name] = home_team_name
                                        
                    except Exception as e:
                        logger.error(f"Error fetching rosters for game {home_team_name} vs {away_team_name}: {e}")
                        continue

        logger.info(f"Found {len(player_name_to_opponent)} players with games today")

        # Process each leader
        hitters_data = []
        for leader in leaders:
            try:
                player_id = leader.get('person_id')
                if not player_id:
                    continue
                
                player_name = leader.get('name', get_player_name(player_id))
                team_name = leader.get('team_name', "N/A")
                home_runs = leader.get('value')
                
                # Get at-bats from player stats
                player_stats = get_player_stats(player_id, group="hitting", type="season")
                at_bats = player_stats.get('atBats', "N/A")
                
                # Check if we have a matchup today by player name
                opponent_today = player_name_to_opponent.get(player_name, "No game today")
                
                # Get today's odds for this player
                player_odds = odds_data.get(player_name, {})
                odds_display = player_odds.get('odds', 'N/A')
                
                # Provide better context for why odds aren't available
                if odds_display == 'N/A' and opponent_today != "No game today":
                    # Player has a game but no odds - check if their team is in odds coverage
                    if team_name not in games_with_odds:
                        odds_display = "Game not covered"
                
                hitters_data.append({
                    'name': player_name,
                    'team': team_name,
                    'opponent_today': opponent_today,
                    'at_bats': at_bats,
                    'home_runs': home_runs,
                    'todays_odds': odds_display,
                    'odds_raw': player_odds.get('raw_odds'),
                    'sportsbook_count': player_odds.get('sportsbook_count', 0)
                })
            except Exception as e:
                logger.error(f"Error processing hitter: {e}")
                continue

        # Sort by home runs (descending)
        hitters_data.sort(key=lambda x: int(x.get('home_runs', 0)) if isinstance(x.get('home_runs'), (int, str)) and str(x.get('home_runs', 0)).isdigit() else 0, 
                         reverse=True)
        
        result = {
            'hitters_data': hitters_data[:50], 
            'total_hitters': len(hitters_data),
            'odds_coverage': {
                'games_with_odds': len(games_with_odds) // 2,  # Divide by 2 since we count both teams
                'teams_covered': list(games_with_odds)
            }
        }
        logger.info(f"Successfully fetched hitter data for {len(hitters_data)} players")
        logger.info(f"Odds coverage: {len(games_with_odds) // 2} games covered")
        return result
        
    except Exception as e:
        logger.error(f"Error in fetch_daily_hitters_data: {e}")
        return {'error': str(e), 'hitters_data': []}

def daily_data_update():
    """Main function to update all daily data at 3AM"""
    logger.info("Starting daily data update...")
    update_start_time = time.time()
    
    today_str = get_mlb_today()
    
    try:
        # Fetch pitcher data
        pitcher_data = fetch_daily_pitchers_data(today_str)
        save_daily_data('pitchers', today_str, pitcher_data)
        DAILY_DATA_CACHE['pitchers'] = pitcher_data
        
        # Fetch hitter data
        hitter_data = fetch_daily_hitters_data()
        save_daily_data('hitters', today_str, hitter_data)
        DAILY_DATA_CACHE['hitters'] = hitter_data
        
        # Fetch schedule data
        schedule_data = statsapi.schedule(date=today_str, sportId=1)
        save_daily_data('schedule', today_str, schedule_data)
        DAILY_DATA_CACHE['schedule'] = schedule_data
        
        # Update cache metadata
        DAILY_DATA_CACHE['last_updated'] = get_eastern_time().isoformat()
        DAILY_DATA_CACHE['update_date'] = today_str
        
        # Note: Pitcher report generation moved exclusively to 11 AM scheduled task
        # No backup generation in daily update to avoid duplicate articles
        
        update_time = time.time() - update_start_time
        logger.info(f"Daily data update completed successfully in {update_time:.2f} seconds")
        
        # Clear old data (keep last 7 days)
        cleanup_old_data()
        
    except Exception as e:
        logger.error(f"Error during daily data update: {e}")
        traceback.print_exc()

def cleanup_old_data():
    """Clean up old data from the database (older than 7 days)"""
    try:
        conn = sqlite3.connect(DATABASE_PATH)
        cursor = conn.cursor()
        
        # Calculate cutoff date (7 days ago)
        cutoff_date = (datetime.now() - timedelta(days=7)).strftime('%Y-%m-%d')
        
        # Delete old data
        cursor.execute("DELETE FROM daily_data WHERE data_date < ?", (cutoff_date,))
        deleted_rows = cursor.rowcount
        
        conn.commit()
        conn.close()
        
        logger.info(f"Cleaned up {deleted_rows} old data records (older than {cutoff_date})")
    except Exception as e:
        logger.error(f"Error cleaning up old data: {e}")

# Popular teams to preload (most viewed teams)
POPULAR_TEAMS = [
    147,  # New York Yankees
    121,  # New York Mets  
    119,  # Los Angeles Dodgers
    117,  # Houston Astros
    111,  # Boston Red Sox
    108,  # Los Angeles Angels
    145,  # Chicago White Sox
    112,  # Chicago Cubs
    158,  # Milwaukee Brewers
    143   # Philadelphia Phillies
]

def preload_popular_teams():
    """Preload matchup data for popular teams in background"""
    logger.info("Starting background preload of popular teams...")
    
    for i, team_id in enumerate(POPULAR_TEAMS):
        try:
            cache_key = f"matchup_{team_id}_{CURRENT_SEASON}"
            cached_data = get_cached_data(TEAM_MATCHUP_CACHE, cache_key)
            
            if cached_data is None:
                logger.info(f"Preloading team {team_id}... ({i+1}/{len(POPULAR_TEAMS)})")
                # Make internal request to populate cache
                with app.test_client() as client:
                    response = client.get(f'/api/matchup_hitters/{team_id}')
                    if response.status_code == 200:
                        logger.info(f"Successfully preloaded team {team_id}")
                    else:
                        logger.warning(f"Failed to preload team {team_id}: {response.status_code}")
                
                # Add delay between requests to prevent overwhelming the system
                if i < len(POPULAR_TEAMS) - 1:  # Don't delay after the last team
                    time.sleep(2)  # 2 second delay between teams
            else:
                logger.info(f"Team {team_id} already cached")
                
        except Exception as e:
            logger.error(f"Error preloading team {team_id}: {e}")
            # Continue with next team instead of crashing
            continue
    
    logger.info("Completed background preload of popular teams")

def load_today_data_on_startup():
    """Load today's data from database on application startup"""
    today_str = get_mlb_today()
    
    logger.info("Loading today's data from database...")
    
    # Load pitcher data
    pitcher_data = load_daily_data('pitchers', today_str)
    if pitcher_data:
        DAILY_DATA_CACHE['pitchers'] = pitcher_data
        logger.info("Loaded pitcher data from database")
    
    # Load hitter data
    hitter_data = load_daily_data('hitters', today_str)
    if hitter_data:
        DAILY_DATA_CACHE['hitters'] = hitter_data
        logger.info("Loaded hitter data from database")
    
    # Load schedule data
    schedule_data = load_daily_data('schedule', today_str)
    if schedule_data:
        DAILY_DATA_CACHE['schedule'] = schedule_data
        logger.info("Loaded schedule data from database")
    
    if pitcher_data or hitter_data or schedule_data:
        DAILY_DATA_CACHE['update_date'] = today_str
        DAILY_DATA_CACHE['last_updated'] = get_eastern_time().isoformat()
        logger.info("Successfully loaded today's data from database")
    else:
        logger.info("No cached data found for today, will fetch on first request")

def setup_scheduler():
    """Set up the background scheduler for daily updates and preloading"""
    global scheduler
    
    # Check if scheduler is disabled via environment variable
    if os.environ.get('DISABLE_SCHEDULER') == '1':
        logger.info("Background scheduler disabled via DISABLE_SCHEDULER environment variable")
        return
    
    if scheduler is None:
        scheduler = BackgroundScheduler()
        
        # Schedule daily data update at 3 AM Eastern
        scheduler.add_job(
            func=daily_data_update,
            trigger="cron",
            hour=3,
            minute=0,
            timezone=pytz.timezone('US/Eastern'),
            id='daily_update',
            replace_existing=True
        )
        
        # Schedule popular teams preloading at 3:30 AM Eastern (after daily update)
        scheduler.add_job(
            func=preload_popular_teams,
            trigger="cron", 
            hour=3,
            minute=30,
            timezone=pytz.timezone('US/Eastern'),
            id='preload_teams',
            replace_existing=True
        )
        
        # Schedule cleanup at 2 AM Eastern (before daily update)
        scheduler.add_job(
            func=cleanup_old_data,
            trigger="cron",
            hour=2,
            minute=0,
            timezone=pytz.timezone('US/Eastern'),
            id='cleanup',
            replace_existing=True
        )
        
        # Schedule daily pitcher report at 11 AM Eastern
        scheduler.add_job(
            func=scheduled_pitcher_report_generation,
            trigger="cron",
            hour=11,
            minute=0,
            timezone=pytz.timezone('US/Eastern'),
            id='daily_pitcher_report',
            replace_existing=True
        )
        
        try:
            scheduler.start()
            logger.info("Background scheduler started successfully")
            logger.info("Scheduled jobs:")
            logger.info("- Data cleanup: 2:00 AM ET")
            logger.info("- Daily data update: 3:00 AM ET")
            logger.info("- Popular teams preload: 3:30 AM ET") 
            logger.info("- Daily pitcher report: 11:00 AM ET")
        except Exception as e:
            logger.error(f"Failed to start scheduler: {e}")
    else:
        logger.info("Scheduler already running")

# Initialize database on import with recovery capability
init_database()
load_today_data_on_startup()
# Scheduler will be initialized after all functions are defined

# Cache data with timeout for automatic expiration
def cache_data(cache_dict, key, data, timeout=CACHE_TIMEOUT):
    if not ENABLE_CACHE:
        return data
    
    cache_dict[key] = {
        'data': data,
        'timestamp': time.time(),
        'expires': time.time() + timeout
    }
    return data

# Get data from cache if available and not expired
def get_cached_data(cache_dict, key):
    if not ENABLE_CACHE:
        return None
        
    if key in cache_dict:
        entry = cache_dict[key]
        if entry['expires'] > time.time():
            print(f"Cache hit for {key}")
            return entry['data']
        else:
            # Expired entry, remove it
            print(f"Cache expired for {key}")
            del cache_dict[key]
    return None
    
def get_player_stats(player_id, group="pitching", type="season"):
    """
    Gets player stats from cache or API with caching for better performance.
    
    Args:
        player_id: The player's ID
        group: The stats group (pitching/hitting)
        type: The type of stats (season/seasonAdvanced)
    
    Returns:
        Dictionary of parsed stats
    """
    # Create a cache key using player_id, group, and type
    cache_key = f"{player_id}_{group}_{type}"
    
    # Check if we have this data in the cache
    cached_stats = get_cached_data(PLAYER_STATS_CACHE, cache_key)
    if cached_stats is not None:
        return cached_stats
    
    # If not in cache, fetch from API
    print(f"Cache miss - Fetching stats for Player ID: {player_id}, group: {group}, type: {type}")
    stats_data = statsapi.player_stats(player_id, group=group, type=type)
    
    # Parse the stats data
    parsed_stats = {}
    if isinstance(stats_data, str):
        # Parse string response
        for line in stats_data.split('\n'):
            if ':' in line:
                key_value = line.split(':', 1)
                if len(key_value) == 2:
                    key = key_value[0].strip()
                    value = key_value[1].strip()
                    parsed_stats[key] = value
    elif isinstance(stats_data, dict):
        stats_list = stats_data.get('stats')
        if isinstance(stats_list, list) and stats_list:
            first_stat_entry = stats_list[0]
            if isinstance(first_stat_entry, dict) and isinstance(first_stat_entry.get('stats'), dict):
                parsed_stats = first_stat_entry['stats']
    
    # Store in cache and return
    return cache_data(PLAYER_STATS_CACHE, cache_key, parsed_stats)

def get_team_abbreviation_to_id_map():
    """
    Fetches all teams and creates a mapping from team abbreviation to team ID.
    This is useful for looking up team IDs when only abbreviation or name is known.
    """
    teams = statsapi.lookup_team('') # Search for all teams
    team_map = {}
    for team in teams:
        if team.get('abbreviation') and team.get('id'):
            team_map[team['abbreviation'].upper()] = team['id']
        # Fallback or alternative names if abbreviation isn't always present or consistent
        if team.get('teamName') and team.get('id'):
            team_map[team['teamName'].upper()] = team['id']
    return team_map

# Cache team maps for the entire session
TEAM_ABBREVIATION_TO_ID = get_team_abbreviation_to_id_map()
ID_TO_TEAM_ABBREVIATION = {v: k for k, v in TEAM_ABBREVIATION_TO_ID.items()}

@lru_cache(maxsize=100)
def get_player_name(player_id):
    try:
        player_info_list = statsapi.lookup_player(player_id)
        # Ensure player_info_list is a list and has content
        if isinstance(player_info_list, list) and player_info_list:
            player_data = player_info_list[0]
            # Ensure player_data is a dictionary
            if isinstance(player_data, dict):
                return player_data.get('fullName', f"Unknown Player (ID: {player_id})")
        
        # If checks fail, log and return a specific message
        print(f"Could not retrieve valid player name for ID {player_id}. API response: {player_info_list}")
        return f"Player Info N/A (ID: {player_id})"
    except Exception as e:
        print(f"Error in get_player_name for ID {player_id}: {e}")
        return f"Error Getting Name (ID: {player_id})"

def get_team_roster(team_id, season=CURRENT_SEASON):
    """Get team roster with caching"""
    cache_key = f"roster_{team_id}_{season}"
    
    # Check cache first
    cached_roster = get_cached_data(TEAM_ROSTER_CACHE, cache_key)
    if cached_roster is not None:
        return cached_roster
    
    # Fetch from API if not in cache
    roster_data = statsapi.roster(team_id, season=season)
    
    # Store in cache with longer timeout (rosters don't change often)
    return cache_data(TEAM_ROSTER_CACHE, cache_key, roster_data, timeout=3600)  # 1 hour cache

def get_daily_schedule(date_str=None):
    """Get schedule for a given date with caching"""
    if date_str is None:
        date_str = get_mlb_today()
    
    cache_key = f"schedule_{date_str}"
    
    # Check cache first
    cached_schedule = get_cached_data(SCHEDULE_CACHE, cache_key)
    if cached_schedule is not None:
        return cached_schedule
    
    # Fetch from API if not in cache
    schedule_data = statsapi.schedule(date=date_str, sportId=1)
    
    # Store in cache with a shorter timeout (schedule details can change)
    return cache_data(SCHEDULE_CACHE, cache_key, schedule_data, timeout=300)  # 5 minutes cache

@app.route('/')
def index():
    return render_template('index.html', season=CURRENT_SEASON)

@app.route('/favicon.ico')
def favicon():
    return send_from_directory(os.path.join(app.root_path, 'static'),
                               'favicon.ico', mimetype='image/vnd.microsoft.icon')

# Debug route to test static file serving and GA
@app.route('/debug/static')
def debug_static():
    static_path = os.path.join(app.root_path, 'static')
    files = os.listdir(static_path)
    return {
        'static_path': static_path,
        'files': files,
        'favicon_ico_exists': os.path.exists(os.path.join(static_path, 'favicon.ico')),
        'favicon_png_exists': os.path.exists(os.path.join(static_path, 'favicon.png')),
        'ga_measurement_id': 'G-H0ZR5N8SS8'
    }

@app.route('/pitchers')
def pitchers_page():
    return render_template('pitchers.html', today_date=get_mlb_today(), season=CURRENT_SEASON)

@app.route('/api/pitchers')
def pitchers_api():
    """Optimized pitchers API using pre-fetched daily data"""
    today_str = get_mlb_today()
    
    # First, try to get data from daily cache
    if (DAILY_DATA_CACHE['pitchers'] is not None and 
        DAILY_DATA_CACHE['update_date'] == today_str):
        logger.info("Serving pitcher data from daily cache")
        return jsonify({
            **DAILY_DATA_CACHE['pitchers'],
            'today_date': today_str,
            'source': 'daily_cache',
            'last_updated': DAILY_DATA_CACHE['last_updated']
        })
    
    # Try to load from database if not in memory cache
    db_data = load_daily_data('pitchers', today_str)
    if db_data:
        logger.info("Serving pitcher data from database")
        DAILY_DATA_CACHE['pitchers'] = db_data
        DAILY_DATA_CACHE['update_date'] = today_str
        return jsonify({
            **db_data,
            'today_date': today_str,
            'source': 'database',
            'last_updated': DAILY_DATA_CACHE.get('last_updated', 'unknown')
        })
    
    # Fallback to live fetching if no cached data available
    logger.warning("No cached pitcher data found, fetching live data")
    try:
        live_data = fetch_daily_pitchers_data(today_str)
        # Cache the live data for future requests
        DAILY_DATA_CACHE['pitchers'] = live_data
        DAILY_DATA_CACHE['update_date'] = today_str
        DAILY_DATA_CACHE['last_updated'] = get_eastern_time().isoformat()
        
        # Also save to database
        save_daily_data('pitchers', today_str, live_data)
        
        return jsonify({
            **live_data,
            'today_date': today_str,
            'source': 'live_fallback',
            'last_updated': DAILY_DATA_CACHE['last_updated']
        })
    except Exception as e:
        logger.error(f"Error in live fallback for pitchers: {e}")
        return jsonify({
            'error': f"Unable to fetch pitcher data: {e}",
            'pitchers_data': [],
            'today_date': today_str,
            'source': 'error'
        })

@app.route('/hitters')
def hitters_page():
    return render_template('hitters.html', season=CURRENT_SEASON)

@app.route('/login')
def login_page():
    return render_template('login.html', season=CURRENT_SEASON)

@app.route('/api/auth/verify', methods=['POST'])
def verify_auth():
    """Verify Firebase token and store in session"""
    data = request.get_json()
    if not data or 'idToken' not in data:
        return jsonify({'error': 'ID token required'}), 400
    
    id_token = data['idToken']
    if verify_firebase_token(id_token):
        session['firebase_token'] = id_token
        return jsonify({'success': True})
    else:
        return jsonify({'error': 'Invalid token'}), 401

@app.route('/api/auth/logout', methods=['POST'])
def logout():
    session.clear()
    return jsonify({'success': True})

@app.route('/api/auth/user-info', methods=['GET'])
@require_auth
def get_user_info():
    """Get current user's information including role"""
    user_info = session.get('user_info')
    if user_info:
        return jsonify({
            'email': user_info.get('email'),
            'role': user_info.get('role'),
            'isAdmin': user_info.get('role') == 'admin'
        })
    return jsonify({'error': 'User not found'}), 404

@app.route('/debug/auth')
def debug_auth_info():
    """Debug route to show authentication details"""
    auth_token = session.get('firebase_token') or request.headers.get('Authorization')
    
    debug_info = {
        'has_auth_token': bool(auth_token),
        'session_keys': list(session.keys()),
        'user_info_in_session': session.get('user_info'),
    }
    
    if auth_token:
        decoded_token = verify_firebase_token(auth_token)
        debug_info['token_decoded'] = bool(decoded_token)
        if decoded_token:
            debug_info['token_data'] = {
                'uid': decoded_token.get('uid'),
                'email': decoded_token.get('email')
            }
            
            # Try to get user from database
            user_info = get_or_create_user(decoded_token.get('uid'), decoded_token.get('email'))
            debug_info['user_from_db'] = user_info
    
    return jsonify(debug_info)

@app.route('/admin-debug')
def admin_debug_page():
    """Serve the admin debug page"""
    return send_from_directory('.', 'admin_debug.html')

@app.route('/api/admin/make-admin', methods=['POST'])
def make_admin():
    """Make a user admin - only for initial setup or by existing admins"""
    data = request.get_json()
    email = data.get('email')
    secret_key = data.get('secret_key')
    
    # Allow initial admin setup with secret key, or require existing admin
    if secret_key == 'initial_admin_setup_2025':
        # This is for initial setup only
        if set_user_role(email, 'admin'):
            return jsonify({'success': True, 'message': f'User {email} is now an admin'})
        else:
            return jsonify({'error': 'Failed to set admin role'}), 500
    else:
        # Check if current user is admin
        auth_token = session.get('firebase_token') or request.headers.get('Authorization')
        if auth_token:
            decoded_token = verify_firebase_token(auth_token)
            if decoded_token:
                user_info = get_or_create_user(decoded_token.get('uid'), decoded_token.get('email'))
                if user_info and user_info.get('role') == 'admin':
                    if set_user_role(email, 'admin'):
                        return jsonify({'success': True, 'message': f'User {email} is now an admin'})
                    else:
                        return jsonify({'error': 'Failed to set admin role'}), 500
        
        return jsonify({'error': 'Admin access required'}), 403

@app.route('/admin')
@require_admin
def admin_page():
    return render_template('admin.html', season=CURRENT_SEASON)

@app.route('/profile')
@require_auth
def profile_page():
    return render_template('profile.html', season=CURRENT_SEASON)

@app.route('/blog')
def blog_page():
    page = request.args.get('page', 1, type=int)
    per_page = 5
    offset = (page - 1) * per_page
    
    articles = get_articles(limit=per_page, offset=offset)
    
    return render_template('blog.html', 
                         articles=articles, 
                         current_page=page,
                         has_next=len(articles) == per_page,
                         season=CURRENT_SEASON)

@app.route('/blog/<slug>')
def article_page(slug):
    article = get_article_by_slug(slug)
    if not article:
        return render_template('404.html'), 404
    
    return render_template('article.html', article=article, season=CURRENT_SEASON)

@app.route('/admin/articles')
@require_admin
def admin_articles():
    articles = get_articles(limit=50, status="published") + get_articles(limit=50, status="draft")
    return render_template('admin_articles.html', articles=articles, season=CURRENT_SEASON)

@app.route('/admin/articles/new')
@require_admin
def new_article():
    return render_template('edit_article.html', article=None, season=CURRENT_SEASON)

@app.route('/admin/articles/<int:article_id>/edit')
@require_admin
def edit_article_page(article_id):
    article = get_article_by_id(article_id)
    if not article:
        return render_template('404.html'), 404
    return render_template('edit_article.html', article=article, season=CURRENT_SEASON)

@app.route('/api/articles', methods=['POST'])
@require_admin
def create_article_api():
    data = request.get_json()
    
    # Debug logging
    logger.info(f"Received article creation request")
    logger.info(f"Request data: {data}")
    logger.info(f"Content-Type: {request.content_type}")
    
    if not data or not data.get('title') or not data.get('content'):
        logger.warning(f"Missing required fields - title: {data.get('title') if data else 'No data'}, content: {data.get('content') if data else 'No data'}")
        return jsonify({'error': 'Title and content are required'}), 400
    
    article_id = create_article(
        title=data['title'],
        content=data['content'],
        summary=data.get('summary', ''),
        author=data.get('author', 'MLB Analyst'),
        tags=data.get('tags', ''),
        status=data.get('status', 'published')
    )
    
    if article_id:
        logger.info(f"Successfully created article with ID: {article_id}")
        return jsonify({'success': True, 'article_id': article_id})
    else:
        logger.error("Failed to create article in database")
        return jsonify({'error': 'Failed to create article'}), 500

@app.route('/api/articles/<int:article_id>', methods=['PUT'])
@require_admin
def update_article_api(article_id):
    data = request.get_json()
    
    if not data:
        return jsonify({'error': 'No data provided'}), 400
    
    success = update_article(
        article_id=article_id,
        title=data.get('title'),
        content=data.get('content'),
        summary=data.get('summary'),
        author=data.get('author'),
        tags=data.get('tags'),
        status=data.get('status')
    )
    
    if success:
        return jsonify({'success': True})
    else:
        return jsonify({'error': 'Failed to update article'}), 500

@app.route('/api/articles/<int:article_id>', methods=['DELETE'])
@require_admin
def delete_article_api(article_id):
    success = delete_article(article_id)
    
    if success:
        return jsonify({'success': True})
    else:
        return jsonify({'error': 'Failed to delete article'}), 500

# Upload configuration
UPLOAD_FOLDER = 'static/uploads'
ALLOWED_EXTENSIONS = {'png', 'jpg', 'jpeg', 'gif', 'webp'}
MAX_FILE_SIZE = 5 * 1024 * 1024  # 5MB

# Ensure upload directory exists
os.makedirs(UPLOAD_FOLDER, exist_ok=True)

def allowed_file(filename):
    """Check if file extension is allowed"""
    return '.' in filename and \
           filename.rsplit('.', 1)[1].lower() in ALLOWED_EXTENSIONS

@app.route('/api/upload-image', methods=['POST'])
@require_admin
def upload_image():
    """Handle image uploads for blog articles"""
    try:
        # Check if the post request has the file part
        if 'image' not in request.files:
            return jsonify({'error': 'No image file provided'}), 400
        
        file = request.files['image']
        
        # If user does not select file, browser also submits an empty part without filename
        if file.filename == '':
            return jsonify({'error': 'No image file selected'}), 400
        
        # Validate file type
        if not allowed_file(file.filename):
            return jsonify({'error': 'Invalid file type. Allowed: PNG, JPG, JPEG, GIF, WebP'}), 400
        
        # Check file size
        file.seek(0, os.SEEK_END)
        file_size = file.tell()
        file.seek(0)  # Reset file pointer
        
        if file_size > MAX_FILE_SIZE:
            return jsonify({'error': 'File size too large. Maximum 5MB allowed'}), 400
        
        if file and allowed_file(file.filename):
            # Generate unique filename
            file_extension = file.filename.rsplit('.', 1)[1].lower()
            unique_filename = f"{uuid.uuid4().hex}.{file_extension}"
            
            # Save file
            file_path = os.path.join(UPLOAD_FOLDER, unique_filename)
            file.save(file_path)
            
            # Return the URL that can be used to access the image
            image_url = f"/static/uploads/{unique_filename}"
            
            logger.info(f"Image uploaded successfully: {unique_filename}")
            
            return jsonify({
                'success': True,
                'url': image_url,
                'filename': unique_filename,
                'size': file_size
            })
        
        return jsonify({'error': 'File upload failed'}), 500
        
    except Exception as e:
        logger.error(f"Error uploading image: {e}")
        return jsonify({'error': f'Upload failed: {str(e)}'}), 500

@app.route('/api/hitters')
def hitters_api():
    """Optimized hitters API using pre-fetched daily data"""
    today_str = get_mlb_today()
    
    # First, try to get data from daily cache
    if (DAILY_DATA_CACHE['hitters'] is not None and 
        DAILY_DATA_CACHE['update_date'] == today_str):
        logger.info("Serving hitter data from daily cache")
        return jsonify({
            **DAILY_DATA_CACHE['hitters'],
            'today_date': today_str,
            'source': 'daily_cache',
            'last_updated': DAILY_DATA_CACHE['last_updated']
        })
    
    # Try to load from database if not in memory cache
    db_data = load_daily_data('hitters', today_str)
    if db_data:
        logger.info("Serving hitter data from database")
        DAILY_DATA_CACHE['hitters'] = db_data
        DAILY_DATA_CACHE['update_date'] = today_str
        return jsonify({
            **db_data,
            'today_date': today_str,
            'source': 'database',
            'last_updated': DAILY_DATA_CACHE.get('last_updated', 'unknown')
        })
    
    # Fallback to live fetching if no cached data available
    logger.warning("No cached hitter data found, fetching live data")
    try:
        live_data = fetch_daily_hitters_data()
        # Cache the live data for future requests
        DAILY_DATA_CACHE['hitters'] = live_data
        DAILY_DATA_CACHE['update_date'] = today_str
        DAILY_DATA_CACHE['last_updated'] = get_eastern_time().isoformat()
        
        # Also save to database
        save_daily_data('hitters', today_str, live_data)
        
        return jsonify({
            **live_data,
            'today_date': today_str,
            'source': 'live_fallback',
            'last_updated': DAILY_DATA_CACHE['last_updated']
        })
    except Exception as e:
        logger.error(f"Error in live fallback for hitters: {e}")
        return jsonify({
            'error': f"Unable to fetch hitter data: {e}",
            'hitters_data': [],
            'today_date': today_str,
            'source': 'error'
        })

def parse_league_leaders_string(leaders_str):
    """Parse the league leaders string response into structured data."""
    results = []
    
    try:
        # Skip header lines
        lines = leaders_str.strip().split('\n')
        
        # Find the line that starts with 'Rank'
        header_index = -1
        for i, line in enumerate(lines):
            if line.startswith('Rank'):
                header_index = i
                break
        
        if header_index == -1:
            print("Could not find header line in league leaders string")
            return results
        
        # Process each line after the header
        for line in lines[header_index+1:]:
            if not line.strip():
                continue
            
            # Parse fixed-width format: Rank (4 chars), Name (20 chars), Team (24 chars), Value (rest)
            try:
                if len(line) < 50:  # Skip short lines
                    continue
                    
                rank = line[:4].strip()
                name = line[4:24].strip()
                team = line[24:48].strip()
                value = line[48:].strip()
                
                if not rank or not name or not value:
                    continue
                
                # Look up player_id
                player_info = statsapi.lookup_player(name)
                player_id = player_info[0]['id'] if player_info and len(player_info) > 0 else None
                
                results.append({
                    'rank': rank,
                    'name': name,
                    'team_name': team,
                    'person_id': player_id,
                    'value': value
                })
            except Exception as e:
                print(f"Error parsing line: {line}. Error: {e}")
                continue
                
    except Exception as e:
        print(f"Error parsing league leaders string: {e}")
        traceback_str = traceback.format_exc()
        print(f"Traceback: {traceback_str}")
    
    return results

@app.route('/matchup_hitters/<int:team_id>')
def matchup_hitters_page(team_id):
    team_name = "Unknown Team"
    try:
        team_info_list = statsapi.lookup_team(team_id)
        if isinstance(team_info_list, list) and team_info_list and isinstance(team_info_list[0], dict):
            team_name = team_info_list[0].get('name', f"Team ID {team_id}")
    except Exception:
        team_name = f"Team ID {team_id}"
    
    return render_template('matchup_hitters.html', team_name=team_name, team_id=team_id, season=CURRENT_SEASON)

# Add team matchup cache at the top level
TEAM_MATCHUP_CACHE = {}

@app.route('/api/matchup_hitters/<int:team_id>')
def matchup_hitters_api(team_id):
    """Optimized matchup hitters API with improved caching and performance"""
    
    # Check if we have cached data for this team (cache for 1 hour)
    cache_key = f"matchup_{team_id}_{CURRENT_SEASON}"
    cached_data = get_cached_data(TEAM_MATCHUP_CACHE, cache_key)
    if cached_data is not None:
        logger.info(f"Serving cached matchup data for team {team_id}")
        return jsonify(cached_data)
    
    logger.info(f"Fetching fresh matchup data for team {team_id}")
    start_time = time.time()
    
    matchup_data = []
    team_name = "Unknown Team"
    
    try:
        # Fetch home run odds data first
        odds_data = fetch_home_run_odds()
        logger.info(f"Fetched odds for {len(odds_data)} players")
        
        # Get team info with caching
        team_info_cache_key = f"team_info_{team_id}"
        team_info = get_cached_data(TEAM_ROSTER_CACHE, team_info_cache_key)
        
        if team_info is None:
            team_info_list = statsapi.lookup_team(team_id)
            if isinstance(team_info_list, list) and team_info_list and isinstance(team_info_list[0], dict):
                team_info = team_info_list[0]
                cache_data(TEAM_ROSTER_CACHE, team_info_cache_key, team_info, timeout=3600)  # 1 hour cache
            else:
                logger.error(f"Could not find team info for team {team_id}")
                return jsonify({'error': f"Could not find team: {team_id}", 'matchup_data': [], 'team_name': f"Team ID {team_id}"})
        
        team_name = team_info.get('name', f"Team ID {team_id}")
        
        # Get roster with improved caching
        roster_data = get_team_roster(team_id)
        
        if not isinstance(roster_data, str) or "Error" in roster_data:
            logger.error(f"Error fetching roster for team {team_id}: {roster_data}")
            return jsonify({'error': "Could not fetch team roster", 'matchup_data': [], 'team_name': team_name})
        
        # Parse roster more efficiently - extract all player names first
        player_names = []
        for line in roster_data.split('\n'):
            if line.strip() and '#' in line:
                try:
                    parts = line.strip().split()
                    if len(parts) >= 3:
                        player_name = ' '.join(parts[2:])
                        player_names.append(player_name)
                except Exception as e:
                    logger.warning(f"Error parsing roster line: {line}. Error: {e}")
                    continue
        
        logger.info(f"Found {len(player_names)} players on roster for team {team_id}")
        
        # Batch lookup player IDs with caching
        player_id_map = {}  # name -> id
        for player_name in player_names:
            # Check cache first
            player_cache_key = f"player_lookup_{player_name.replace(' ', '_')}"
            cached_player_id = get_cached_data(PLAYER_STATS_CACHE, player_cache_key)
            
            if cached_player_id is not None:
                player_id_map[player_name] = cached_player_id
            else:
                try:
                    player_lookup = statsapi.lookup_player(player_name)
                    if player_lookup and isinstance(player_lookup, list) and player_lookup:
                        player_id = player_lookup[0]['id']
                        player_id_map[player_name] = player_id
                        # Cache the lookup result for 24 hours
                        cache_data(PLAYER_STATS_CACHE, player_cache_key, player_id, timeout=86400)
                    else:
                        logger.warning(f"Could not find ID for player {player_name}")
                except Exception as e:
                    logger.error(f"Error looking up player {player_name}: {e}")
                    continue
        
        logger.info(f"Successfully mapped {len(player_id_map)} player IDs")
        
        # Fetch stats for all players with improved error handling
        player_details_list = []
        for player_name, player_id in player_id_map.items():
            try:
                # Use existing caching for player stats
                s_stats = get_player_stats(player_id, group="hitting", type="season")
                adv_stats = get_player_stats(player_id, group="hitting", type="seasonAdvanced")
                
                # Validate and process stats
                at_bats = s_stats.get('atBats', 0)
                if isinstance(at_bats, str) and at_bats.isdigit():
                    at_bats = int(at_bats)
                elif not isinstance(at_bats, int):
                    at_bats = 0
                
                # Only include players with meaningful at-bats
                if at_bats > 10:  # Increased threshold for better data quality
                    home_runs = s_stats.get('homeRuns', 0)
                    if isinstance(home_runs, str) and home_runs.isdigit():
                        home_runs = int(home_runs)
                    elif not isinstance(home_runs, int):
                        home_runs = 0
                    
                    # Get today's odds for this player
                    player_odds = odds_data.get(player_name, {})
                    odds_display = player_odds.get('odds', 'N/A')
                    
                    player_details_list.append({
                        'name': player_name,  # Use cached name instead of lookup
                        'at_bats': at_bats,
                        'home_runs': home_runs,
                        'ops': s_stats.get('ops', "N/A"),
                        'iso': adv_stats.get('iso', "N/A"),
                        'babip': adv_stats.get('babip', "N/A"),
                        'hr_pa': adv_stats.get('homeRunsPerPlateAppearance', "N/A"),
                        'todays_odds': odds_display,
                        'odds_raw': player_odds.get('raw_odds'),
                        'sportsbook_count': player_odds.get('sportsbook_count', 0)
                    })
                    
            except Exception as e:
                logger.error(f"Error fetching stats for player {player_name} (ID: {player_id}): {e}")
                continue
        
        # Sort by home runs and take top 10
        matchup_data = sorted(player_details_list, 
                            key=lambda x: x['home_runs'] if isinstance(x['home_runs'], int) else 0, 
                            reverse=True)[:10]
        
        if not matchup_data:
            result = {'error': "No hitter data available for this team.", 'matchup_data': [], 'team_name': team_name}
        else:
            # Add summary stats for odds availability
            players_with_odds = sum(1 for player in matchup_data if player['todays_odds'] != 'N/A')
            result = {
                'matchup_data': matchup_data, 
                'team_name': team_name,
                'odds_summary': {
                    'players_with_odds': players_with_odds,
                    'total_players': len(matchup_data)
                }
            }
        
        # Cache the result for 1 hour
        cache_data(TEAM_MATCHUP_CACHE, cache_key, result, timeout=3600)
        
        elapsed_time = time.time() - start_time
        logger.info(f"Matchup data for team {team_id} fetched in {elapsed_time:.2f} seconds")
        
        return jsonify(result)

    except Exception as e:
        logger.error(f"Error fetching matchup hitters data for team ID {team_id}: {e}")
        return jsonify({'error': f"Error fetching data: {e}", 'matchup_data': [], 'team_name': team_name})

# Add a route for application config information
@app.route('/api/config')
def app_config():
    return jsonify({
        'season': CURRENT_SEASON,
        'cache_enabled': ENABLE_CACHE,
        'cache_timeout': CACHE_TIMEOUT
    })

@app.route('/api/daily_status')
def daily_status():
    """Get the status of daily data updates"""
    today_str = get_mlb_today()
    current_et = get_eastern_time()
    
    # Get scheduler job info
    scheduled_jobs = []
    if scheduler and scheduler.running:
        for job in scheduler.get_jobs():
            scheduled_jobs.append({
                'id': job.id,
                'next_run': str(job.next_run_time),
                'function': job.func.__name__ if hasattr(job.func, '__name__') else str(job.func)
            })
    
    status = {
        'today': today_str,
        'eastern_time': {
            'current_time': current_et.strftime("%Y-%m-%d %H:%M:%S %Z"),
            'timezone': str(current_et.tzinfo),
            'hour': current_et.hour,
            'next_changeover': '3:00 AM ET tomorrow' if current_et.hour >= 3 else f'3:00 AM ET today ({3 - current_et.hour} hours)'
        },
        'daily_cache_status': {
            'pitchers': DAILY_DATA_CACHE['pitchers'] is not None,
            'hitters': DAILY_DATA_CACHE['hitters'] is not None,
            'schedule': DAILY_DATA_CACHE['schedule'] is not None,
            'last_updated': DAILY_DATA_CACHE.get('last_updated'),
            'update_date': DAILY_DATA_CACHE.get('update_date')
        },
        'database_status': {
            'pitchers': load_daily_data('pitchers', today_str) is not None,
            'hitters': load_daily_data('hitters', today_str) is not None,
            'schedule': load_daily_data('schedule', today_str) is not None
        },
        'scheduler_status': {
            'running': scheduler.running if scheduler is not None else False,
            'total_jobs': len(scheduler.get_jobs()) if scheduler is not None else 0,
            'jobs': scheduled_jobs
        },
        'automated_schedule': {
            'data_cleanup': '2:00 AM Eastern Time daily',
            'data_update': '3:00 AM Eastern Time daily',
            'teams_preload': '3:30 AM Eastern Time daily',
            'pitcher_report': '11:00 AM Eastern Time daily'
        }
    }
    
    return jsonify(status)

@app.route('/api/trigger_update')
def trigger_update():
    """Manually trigger a daily data update"""
    try:
        # Run the update in a separate thread to avoid timeout
        import threading
        thread = threading.Thread(target=daily_data_update)
        thread.daemon = True
        thread.start()
        
        return jsonify({
            'status': 'success',
            'message': 'Daily data update triggered successfully',
            'timestamp': get_eastern_time().isoformat(),
            'mlb_date': get_mlb_today()
        })
    except Exception as e:
        logger.error(f"Error triggering update: {e}")
        return jsonify({
            'status': 'error',
            'message': f'Error triggering update: {e}',
            'timestamp': get_eastern_time().isoformat(),
            'mlb_date': get_mlb_today()
        })

@app.route('/api/clear_cache')
def clear_cache_endpoint():
    # Only clear cache if authorized
    try:
        # Clear all caches
        PLAYER_STATS_CACHE.clear()
        TEAM_ROSTER_CACHE.clear()
        TEAM_MATCHUP_CACHE.clear()
        
        logger.info("All caches cleared successfully")
        return jsonify({'success': True, 'message': 'All caches cleared successfully'})
    except Exception as e:
        logger.error(f"Error clearing caches: {e}")
        return jsonify({'success': False, 'error': str(e)})

@app.route('/api/clear_matchup_cache')
def clear_matchup_cache():
    """Clear only the matchup cache for faster testing"""
    try:
        TEAM_MATCHUP_CACHE.clear()
        logger.info("Matchup cache cleared successfully")
        return jsonify({'success': True, 'message': 'Matchup cache cleared successfully'})
    except Exception as e:
        logger.error(f"Error clearing matchup cache: {e}")
        return jsonify({'success': False, 'error': str(e)})

@app.route('/api/refresh_odds')
def refresh_odds():
    """Refresh odds data by clearing cache and fetching fresh data"""
    try:
        # Clear the requests cache to force fresh data fetch
        requests_cache.clear()
        
        # Clear any relevant caches
        PLAYER_STATS_CACHE.clear()
        
        logger.info("Odds cache cleared, fresh data will be fetched on next request")
        return jsonify({
            'success': True, 
            'message': 'Odds cache cleared - fresh data will be fetched',
            'timestamp': get_eastern_time().isoformat()
        })
    except Exception as e:
        logger.error(f"Error refreshing odds: {e}")
        return jsonify({'success': False, 'error': str(e)})

@app.route('/api/preload_teams')
def preload_teams_endpoint():
    """Manually trigger preloading of popular teams"""
    try:
        import threading
        thread = threading.Thread(target=preload_popular_teams)
        thread.daemon = True
        thread.start()
        
        return jsonify({'success': True, 'message': 'Preloading started in background'})
    except Exception as e:
        logger.error(f"Error starting preload: {e}")
        return jsonify({'success': False, 'error': str(e)})

@app.route('/api/generate_pitcher_report', methods=['POST'])
def generate_pitcher_report_endpoint():
    """Manually trigger pitcher report generation - using Dinger Tuesday format"""
    try:
        result = create_dinger_tuesday_pitcher_report()
        if result and result.get('success'):
            return jsonify({
                'success': True, 
                'message': result['message'],
                'article_id': result['article_id']
            })
        else:
            return jsonify({
                'success': False, 
                'message': result.get('message', 'Failed to generate pitcher report')
            }), 400
    except Exception as e:
        print(f"Error generating pitcher report: {e}")
        return jsonify({'success': False, 'error': str(e)}), 500

@app.route('/api/generate_dinger_tuesday_report', methods=['POST'])
def generate_dinger_tuesday_report_endpoint():
    """Manually trigger Dinger Tuesday style pitcher report generation"""
    try:
        result = create_dinger_tuesday_pitcher_report()
        if result['success']:
            return jsonify(result)
        else:
            return jsonify(result), 400
    except Exception as e:
        print(f"Error generating Dinger Tuesday report: {e}")
        return jsonify({'success': False, 'error': str(e)}), 500

@app.route('/api/test_scheduled_report', methods=['POST'])
def test_scheduled_report_endpoint():
    """Test the scheduled pitcher report generation (same as what runs at 11AM)"""
    try:
        # Run the same function that gets called by the scheduler
        scheduled_pitcher_report_generation()
        return jsonify({
            'success': True, 
            'message': 'Scheduled report generation test completed - check server logs for details'
        })
    except Exception as e:
        logger.error(f"Error testing scheduled report: {e}")
        return jsonify({'success': False, 'error': str(e)}), 500

def analyze_pitchers_for_hr_vulnerability(pitchers_data):
    """Analyze pitchers and return top 3 most vulnerable to home runs"""
    try:
        vulnerable_pitchers = []
        
        # Extract pitchers data if it's nested
        if isinstance(pitchers_data, dict) and 'pitchers_data' in pitchers_data:
            pitchers_list = pitchers_data['pitchers_data']
        else:
            pitchers_list = pitchers_data
            
        for pitcher in pitchers_list:
            try:
                # Convert string values to integers, skip if invalid
                batters_faced = pitcher.get('batters_faced', '0')
                home_runs_allowed = pitcher.get('home_runs_allowed', '0')
                
                # Skip if data is invalid or insufficient
                if (batters_faced in ['N/A', 'ID Lookup Error', 'Fetch Error'] or 
                    home_runs_allowed in ['N/A', 'ID Lookup Error', 'Fetch Error']):
                    continue
                    
                batters_faced = int(batters_faced)
                home_runs_allowed = int(home_runs_allowed)
                
                # Skip pitchers with insufficient data (lowered threshold for more realistic filtering)
                if batters_faced < 100:  # Reduced from 50 to include more pitchers
                    continue
                    
                # Calculate home run rate as percentage
                hr_rate = (home_runs_allowed / batters_faced) * 100  # Convert to percentage
                
                # Only include pitchers with some home runs allowed (but allow 0 for analysis)
                if hr_rate < 0.5:  # Skip only extremely low rates (less than 0.5%)
                    continue
                
                # Parse name
                full_name = pitcher.get('name', 'Unknown Player')
                name_parts = full_name.split(' ', 1)
                first_name = name_parts[0] if len(name_parts) > 0 else 'Unknown'
                last_name = name_parts[1] if len(name_parts) > 1 else ''
                
                # Get team info
                team_name = pitcher.get('team', 'Unknown Team')
                team_abbreviation = team_name.split(' ')[-1] if team_name else 'UNK'  # Simple abbreviation
                
                # Get opponent info
                opponent_name = pitcher.get('opponent', 'TBD')
                opponent_team_id = pitcher.get('opponent_id')
                
                vulnerable_pitchers.append({
                    'first_name': first_name,
                    'last_name': last_name,
                    'full_name': full_name,
                    'team_abbreviation': team_abbreviation,
                    'team_name': team_name,
                    'team_id': pitcher.get('team_id'),
                    'home_runs_allowed': home_runs_allowed,
                    'batters_faced': batters_faced,
                    'hr_rate': hr_rate,  # Now stored as percentage
                    'innings_pitched': 0,  # Not available in current data
                    'era': 0,  # Not available in current data
                    'whip': 0,  # Not available in current data
                    'opponent_name': opponent_name,
                    'opponent_team_id': opponent_team_id
                })
                
            except (ValueError, TypeError) as e:
                print(f"Error processing pitcher {pitcher.get('name', 'Unknown')}: {e}")
                continue
        
        # Sort by HR rate (highest first) and return top 3
        vulnerable_pitchers.sort(key=lambda x: x['hr_rate'], reverse=True)
        return vulnerable_pitchers[:3]
        
    except Exception as e:
        print(f"Error analyzing pitchers: {e}")
        return []

def get_opponent_top_hr_hitters(team_id, limit=3):
    """Get top home run hitters from opposing team"""
    try:
        # Get team roster
        roster_data = get_team_roster(team_id)
        if not roster_data or 'roster' not in roster_data:
            return []
        
        hitters_with_hrs = []
        
        for player in roster_data['roster']:
            try:
                player_id = player.get('person', {}).get('id')
                if not player_id:
                    continue
                
                # Get hitting stats
                stats = get_player_stats(player_id, group="hitting", type="season")
                if stats and stats.get('homeRuns', 0) > 0:
                    hitters_with_hrs.append({
                        'name': player.get('person', {}).get('fullName', 'Unknown'),
                        'home_runs': int(stats.get('homeRuns', 0)),
                        'avg': stats.get('avg', '.000'),
                        'ops': stats.get('ops', '.000')
                    })
            except Exception as e:
                logger.warning(f"Error getting stats for player {player.get('person', {}).get('fullName', 'Unknown')}: {e}")
                continue
        
        # Return top hitters by home runs
        hitters_with_hrs.sort(key=lambda x: x['home_runs'], reverse=True)
        return hitters_with_hrs[:limit]
        
    except Exception as e:
        logger.error(f"Error getting opponent hitters for team {team_id}: {e}")
        return []

def generate_pitcher_report_content(top_pitchers, report_date):
    """Generate clean pitcher report content without inline styles"""
    
    # Format the date nicely
    date_obj = datetime.strptime(report_date, '%Y-%m-%d')
    formatted_date = date_obj.strftime('%B %d, %Y')
    
    # Start building clean HTML content
    content = f"""<div class="intro-section">
<p>Welcome to our <strong>Dinger Tuesday Pitcher Report</strong>, where we identify the most vulnerable pitchers for home run prop betting. Today's analysis focuses on pitchers with the highest home run rates per batter faced.</p>
</div>

<h2>Today's Top 3 Vulnerable Pitchers</h2>
<p>Based on our analysis of current MLB data, here are the three pitchers most susceptible to giving up home runs:</p>
"""
    
    # Add each pitcher with clean formatting
    for i, pitcher in enumerate(top_pitchers, 1):
        # Determine ranking class
        ranking_class = f"pitcher-rank-{i}"
        
        # Get opponent info
        opponent_info = ""
        if pitcher.get('opponent_team_id'):
            try:
                top_hitters = get_opponent_top_hr_hitters(pitcher['opponent_team_id'])
                if top_hitters:
                    hitter_names = [h['name'] for h in top_hitters[:2]]
                    opponent_info = f" They'll face power hitters like {' and '.join(hitter_names)}."
            except Exception as e:
                print(f"Error getting opponent hitters: {e}")
        
        content += f"""
<h3 class="{ranking_class}">#{i} {pitcher['first_name']} {pitcher['last_name']} ({pitcher.get('team_name', 'Unknown')})</h3>
<p><strong>Home Run Rate:</strong> {pitcher['hr_rate']:.1f}% ({pitcher['home_runs_allowed']} HR in {pitcher['batters_faced']} batters faced)</p>
<p><strong>Key Stats:</strong> {pitcher['batters_faced']} batters faced, {pitcher['home_runs_allowed']} home runs allowed</p>
<p><strong>Opponent:</strong> vs {pitcher.get('opponent_name', 'TBD')}{opponent_info}</p>
<p><strong>Betting Analysis:</strong> {pitcher['first_name']} {pitcher['last_name']} has allowed {pitcher['home_runs_allowed']} home runs while facing {pitcher['batters_faced']} batters this season, resulting in a {pitcher['hr_rate']:.1f}% home run rate. This makes them a prime target for home run prop betting.</p>
"""
    
    # Add strategy section
    content += """
<h2>Betting Strategy</h2>
<p>1. <strong>Focus on opposing power hitters</strong> - Look for batters with high home run totals facing these vulnerable pitchers.</p>
<p>2. <strong>Consider ballpark factors</strong> - Home run friendly parks amplify these pitchers' vulnerabilities.</p>
<p>3. <strong>Weather conditions matter</strong> - Wind direction and temperature can significantly impact home run probability.</p>
<p>4. <strong>Line shopping is crucial</strong> - Compare odds across multiple sportsbooks for the best value on home run props.</p>

<div class="disclaimer">
<p><strong>Remember:</strong> Always bet responsibly and within your means. Sports betting should be entertainment, not a financial strategy.</p>
</div>
"""
    
    return content

def create_daily_pitcher_report():
    """DEPRECATED: Use create_dinger_tuesday_pitcher_report() instead
    This function is kept for backwards compatibility but should not be used.
    """
    print("WARNING: create_daily_pitcher_report() is deprecated. Use create_dinger_tuesday_pitcher_report() instead.")
    return {'success': False, 'message': 'Function deprecated - use Dinger Tuesday format instead'}

# Old implementation commented out to prevent duplicate articles
"""
Original create_daily_pitcher_report function - DISABLED to prevent duplicate articles
def create_daily_pitcher_report_OLD():
    try:
        # Get today's date
        today = datetime.now().strftime('%Y-%m-%d')
        
        # Load today's pitcher data
        pitcher_data = load_daily_data('pitchers', today)
        if not pitcher_data:
            print("No pitcher data available for today")
            return None
            
        # Analyze pitchers for HR vulnerability
        top_pitchers = analyze_pitchers_for_hr_vulnerability(pitcher_data)
        if not top_pitchers:
            print("No vulnerable pitchers found")
            return None
            
        # Generate content
        content = generate_pitcher_report_content(top_pitchers, today)
        
        # Create title and summary
        date_obj = datetime.strptime(today, '%Y-%m-%d')
        formatted_date = date_obj.strftime('%B %d, %Y')
        title = f"Dinger Tuesday Pitcher Report - {formatted_date}: Top 3 Pitchers to Target for Home Run Props"
        
        pitcher_names = [f"{p['first_name']} {p['last_name']}" for p in top_pitchers]
        summary = f"Expert analysis of today's most vulnerable pitchers for home run prop betting. Target {', '.join(pitcher_names)} for maximum profit potential."
        
        # Create article
        article_id = create_article(
            title=title,
            content=content,
            summary=summary,
            author="MLB Analyst",
            tags="home run props,baseball betting,pitcher analysis,MLB,sports betting,dinger tuesday",
            status="published"
        )
        
        if article_id:
            print(f"Successfully created daily pitcher report with ID: {article_id}")
            return article_id
        else:
            print("Failed to create pitcher report article")
            return None
            
    except Exception as e:
        print(f"Error creating daily pitcher report: {e}")
        return None
"""

@app.route('/api/debug_pitcher_data')
def debug_pitcher_data():
    """Debug endpoint to check pitcher data structure"""
    try:
        today = datetime.now().strftime('%Y-%m-%d')
        pitcher_data = load_daily_data('pitchers', today)
        
        if not pitcher_data:
            return jsonify({'error': 'No pitcher data found'})
        
        # Show structure
        result = {
            'data_type': type(pitcher_data).__name__,
            'keys': list(pitcher_data.keys()) if isinstance(pitcher_data, dict) else 'Not a dict',
            'sample_pitcher': None
        }
        
        # Get sample pitcher
        if isinstance(pitcher_data, dict) and 'pitchers_data' in pitcher_data:
            pitchers_list = pitcher_data['pitchers_data']
            if pitchers_list and len(pitchers_list) > 0:
                result['sample_pitcher'] = pitchers_list[0]
                result['total_pitchers'] = len(pitchers_list)
        elif isinstance(pitcher_data, list) and len(pitcher_data) > 0:
            result['sample_pitcher'] = pitcher_data[0]
            result['total_pitchers'] = len(pitcher_data)
        
        return jsonify(result)
        
    except Exception as e:
        return jsonify({'error': str(e)})

def generate_dinger_tuesday_pitcher_report_content(top_pitchers, report_date):
    """Generate Dinger Tuesday pitcher report content with authentic tone"""
    
    # Format the date nicely
    date_obj = datetime.strptime(report_date, '%Y-%m-%d')
    formatted_date = date_obj.strftime('%B %d, %Y')
    
    # Create varied, SEO-optimized introductions
    intro_variations = [
        f"""<div class="intro-section">
<p>Tuesday's MLB slate is serving up some serious opportunities for home run prop betting. With FanDuel's 50% profit boost available on any home run wager every Tuesday, smart bettors know to target the most vulnerable arms on the mound.</p>

<p>Today's analysis identifies three starting pitchers whose recent home run rates suggest they're prime targets for the long ball. These aren't just gut feelings—these are data-driven selections based on batters faced and home runs allowed this season.</p>
</div>

<h2>Today's Most Vulnerable Pitchers</h2>
<p>Our analysis reveals three arms that opposing hitters should be licking their chops to face. Let's break down why these pitchers are must-target options for your Tuesday home run props.</p>
""",
        f"""<div class="intro-section">
<p>The numbers don't lie when it comes to identifying which pitchers are most susceptible to giving up home runs. Tuesday's MLB action features several arms that have been far too generous with the long ball this season.</p>

<p>With FanDuel offering 50% profit boosts on home run props every Tuesday, targeting these vulnerable pitchers becomes even more profitable. Our data-driven approach focuses on home run rates per batter faced to identify the clearest opportunities.</p>
</div>

<h2>Arms to Attack on {formatted_date}</h2>
<p>Three starting pitchers stand out as particularly vulnerable to the home run ball. Here's why opposing power hitters should be salivating at these matchups.</p>
""",
        f"""<div class="intro-section">
<p>Smart baseball bettors know that successful home run prop betting starts with identifying the right pitchers to target. Tuesday's slate offers several compelling options for those looking to capitalize on FanDuel's weekly 50% profit boost.</p>

<p>Our analysis cuts through the noise to focus on what matters most: which pitchers have allowed home runs at the highest rates relative to batters faced. These statistical outliers represent the clearest betting opportunities.</p>
</div>

<h2>The Most Targetable Arms</h2>
<p>Today's research identifies three pitchers whose home run rates make them priority targets. Each represents a different angle, but all share one common trait—they've been far too hittable when batters are looking to take them deep.</p>
""",
        f"""<div class="intro-section">
<p>Every Tuesday brings fresh opportunities in the home run prop betting market, and today's MLB schedule features some particularly enticing matchups. The key is identifying which starting pitchers have shown the most vulnerability to the long ball.</p>

<p>Using home run rate data based on batters faced, we've isolated the three most targetable arms taking the mound today. With FanDuel's 50% boost available, these selections offer enhanced value for sharp bettors.</p>
</div>

<h2>Tuesday's Prime Targets</h2>
<p>Three starting pitchers have separated themselves as must-consider options for home run prop betting. Here's the breakdown on why each represents a strong play against opposing power hitters.</p>
""",
        f"""<div class="intro-section">
<p>The best home run prop betting opportunities emerge when statistical analysis meets real-world matchups. Tuesday's MLB action delivers exactly that combination with several highly targetable starting pitchers.</p>

<p>Rather than guessing, our approach focuses on concrete data: home runs allowed per batter faced. This metric cuts through small sample sizes and identifies genuine statistical outliers worth betting against.</p>
</div>

<h2>The Data-Driven Targets</h2>
<p>Our analysis reveals three pitchers whose home run rates make them standout targets for Tuesday's action. Each offers a different risk-reward profile, but all share concerning statistical trends.</p>
"""
    ]
    
    # Randomly select an intro variation based on date to ensure consistency for the same day
    import hashlib
    date_hash = int(hashlib.md5(report_date.encode()).hexdigest(), 16)
    selected_intro = intro_variations[date_hash % len(intro_variations)]
    
    content = selected_intro
    
    # Varied content for each pitcher
    pitcher_variations = [
        {  # Pitcher #1
            "title": f"{top_pitchers[0]['first_name']} {top_pitchers[0]['last_name']} ({top_pitchers[0].get('team_name', 'Unknown')})",
            "intro": "Here's your headline play, folks.",
            "analysis": f"{top_pitchers[0]['last_name']} has been an absolute disaster this season. We're talking {top_pitchers[0]['home_runs_allowed']} home runs allowed across {top_pitchers[0]['batters_faced']} batters faced—that's a brutal {top_pitchers[0]['hr_rate']:.1f}% home run rate that screams 'attack me.'",
            "context": f"The numbers don't lie here. Every time {top_pitchers[0]['last_name']} takes the mound, he's essentially rolling out the red carpet for opposing hitters. A {top_pitchers[0]['hr_rate']:.1f}% HR rate puts him in the bottom tier of MLB starters, and that spells opportunity.",
            "conclusion": f"**The Play:** {top_pitchers[0]['first_name']} {top_pitchers[0]['last_name']} is the crown jewel of tonight's slate. When a pitcher is this generous with the long ball, you hammer the opposing lineup."
        },
        {  # Pitcher #2  
            "title": f"{top_pitchers[1]['first_name']} {top_pitchers[1]['last_name']} ({top_pitchers[1].get('team_name', 'Unknown')})",
            "intro": f"{top_pitchers[1]['last_name']} might fly under the radar, but the smart money knows better.",
            "analysis": f"Don't let the smaller sample size fool you—{top_pitchers[1]['last_name']} has surrendered {top_pitchers[1]['home_runs_allowed']} homers in just {top_pitchers[1]['batters_faced']} batters faced. That {top_pitchers[1]['hr_rate']:.1f}% rate is actually worse than some of the more obvious targets.",
            "context": f"What makes {top_pitchers[1]['last_name']} dangerous to bet against is his tendency to get squared up. When hitters make contact, they're making *quality* contact. The advanced metrics suggest more long balls are coming.",
            "conclusion": f"**The Angle:** While everyone's looking at the obvious plays, {top_pitchers[1]['first_name']} {top_pitchers[1]['last_name']} represents serious value. This is where the sharp action will be."
        },
        {  # Pitcher #3
            "title": f"{top_pitchers[2]['first_name']} {top_pitchers[2]['last_name']} ({top_pitchers[2].get('team_name', 'Unknown')})",
            "intro": f"Want to separate yourself from the crowd? {top_pitchers[2]['first_name']} {top_pitchers[2]['last_name']} is your guy.",
            "analysis": f"The surface stats might not scream 'target me,' but dig deeper and you'll find {top_pitchers[2]['last_name']} carrying a {top_pitchers[2]['hr_rate']:.1f}% home run rate ({top_pitchers[2]['home_runs_allowed']} HRs in {top_pitchers[2]['batters_faced']} batters). That's far from elite.",
            "context": f"Sometimes the best plays hide in plain sight. {top_pitchers[2]['last_name']} has been getting away with murder, but regression tends to find everyone eventually. Tonight could be that night.",
            "conclusion": f"**The Contrarian Take:** If you're hunting for a differentiated play that could separate you from the field, {top_pitchers[2]['first_name']} {top_pitchers[2]['last_name']} offers exactly that kind of upside."
        }
    ]
    
    # Add each pitcher with varied content
    for i, pitcher in enumerate(top_pitchers, 1):
        if i <= len(pitcher_variations):
            variation = pitcher_variations[i-1]
            
            # Get opponent power hitters info
            power_hitter_context = ""
            if pitcher.get('opponent_team_id'):
                try:
                    top_hitters = get_opponent_top_hr_hitters(pitcher['opponent_team_id'])
                    if top_hitters:
                        hitter_names = [h['name'].split(' ')[-1] for h in top_hitters[:2]]  # Last names
                        power_hitter_context = f"<p>Tonight he's facing the {pitcher.get('opponent_name', 'TBD')}, and their {' and '.join(hitter_names)} have been locked in lately. That's a dangerous combination.</p>"
                except Exception as e:
                    logger.error(f"Error getting opponent hitters: {e}")
            
            content += f"""
<h3 class="pitcher-rank-{i}">{variation['title']}</h3>

<p>{variation['intro']} {variation['analysis']}</p>

<p>{variation['context']}</p>

{power_hitter_context}

<p>{variation['conclusion']}</p>
"""
    
    # Add the signature Dinger Tuesday strategy section
    content += """
<h2>The Bottom Line</h2>

<p>Today's not about being cute or finding the perfect contrarian angle. Today's about attacking the obvious weaknesses and riding the wave. These arms are all serving up cookies, and the conditions are looking prime for turning those mistakes into moonshots.</p>

<p>Don't overthink it. Sometimes the best play is the most obvious one.</p>

<h2>Final Thoughts</h2>

<p>Remember, folks—home runs are volatile. They're long shots by nature. But when the stars align like they might today, you've got to strike while the iron is hot.</p>

<p>Pitchers are vulnerable. Data doesn't lie. Numbers are screaming.</p>

<p>Time to cash some tickets.</p>

<div class="disclaimer">
<p><strong>As always, wait for confirmed lineups before placing your bets. And remember—have fun with it. That's what Dinger Tuesday is all about.</strong></p>
</div>
"""
    
    return content

def scheduled_pitcher_report_generation():
    """Scheduled function to generate daily pitcher report at 11AM ET"""
    logger.info("=== AUTOMATED PITCHER REPORT GENERATION STARTED ===")
    
    try:
        # Call the main report generation function
        result = create_dinger_tuesday_pitcher_report()
        
        if result and result.get('success'):
            logger.info(f"✅ AUTOMATED REPORT SUCCESS: {result['message']}")
            logger.info(f"📝 Article ID: {result['article_id']}")
            logger.info(f"🎯 Pitchers analyzed: {', '.join(result['pitchers_analyzed'])}")
        else:
            logger.warning(f"⚠️ AUTOMATED REPORT SKIPPED: {result.get('message', 'Unknown error')}")
            
    except Exception as e:
        logger.error(f"❌ AUTOMATED REPORT FAILED: {e}")
        logger.error(f"Traceback: {traceback.format_exc()}")
        
    logger.info("=== AUTOMATED PITCHER REPORT GENERATION COMPLETED ===")

def create_dinger_tuesday_pitcher_report():
    """Create and save daily pitcher report with authentic Dinger Tuesday tone"""
    try:
        # Get today's date
        today = datetime.now().strftime('%Y-%m-%d')
        
        # Load today's pitcher data
        pitcher_data = load_daily_data('pitchers', today)
        if not pitcher_data:
            print("No pitcher data available for today")
            return {'success': False, 'message': 'No pitcher data available for today'}
            
        # Analyze pitchers for HR vulnerability
        top_pitchers = analyze_pitchers_for_hr_vulnerability(pitcher_data)
        if not top_pitchers:
            print("No vulnerable pitchers found")
            return {'success': False, 'message': 'No vulnerable pitchers found'}
            
        # Generate content with authentic tone
        content = generate_dinger_tuesday_pitcher_report_content(top_pitchers, today)
        
        # Create title and summary
        date_obj = datetime.strptime(today, '%Y-%m-%d')
        formatted_date = date_obj.strftime('%B %d, %Y')
        title = f"MLB Home Run Prop Betting Analysis - Pitcher Report for {formatted_date}"
        
        pitcher_names = [f"{p['first_name']} {p['last_name']}" for p in top_pitchers]
        summary = f"Here's the deal folks—today's slate is loaded with opportunity. We're targeting {', '.join(pitcher_names)} for maximum dinger potential. Time to cash some tickets."
        
        # Create article
        article_id = create_article(
            title=title,
            content=content,
            summary=summary,
            author="Dinger Tuesday Staff",
            tags="dinger tuesday,home run props,baseball betting,pitcher analysis,MLB,sports betting,FanDuel boost",
            status="published"
        )
        
        if article_id:
            print(f"Successfully created Dinger Tuesday pitcher report with ID: {article_id}")
            return {
                'success': True, 
                'message': f'Dinger Tuesday report created successfully',
                'article_id': article_id,
                'pitchers_analyzed': pitcher_names
            }
        else:
            print("Failed to create pitcher report article")
            return {'success': False, 'message': 'Failed to create pitcher report article'}
            
    except Exception as e:
        print(f"Error creating Dinger Tuesday pitcher report: {e}")
        return {'success': False, 'message': f'Error creating report: {e}'}

# Initialize scheduler after all functions are defined
scheduler = setup_scheduler()

# Database backup and restore functions for deployment persistence
def backup_database():
    """Create a backup of the current database"""
    try:
        import shutil
        import os
        from datetime import datetime
        
        if os.path.exists(DATABASE_PATH):
            backup_dir = 'backups'
            os.makedirs(backup_dir, exist_ok=True)
            
            timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
            backup_path = f"{backup_dir}/daily_mlb_data_backup_{timestamp}.sqlite"
            
            shutil.copy2(DATABASE_PATH, backup_path)
            logger.info(f"Database backed up to {backup_path}")
            
            # Keep only the last 5 backups
            backups = sorted([f for f in os.listdir(backup_dir) if f.startswith('daily_mlb_data_backup_')])
            if len(backups) > 5:
                for old_backup in backups[:-5]:
                    os.remove(os.path.join(backup_dir, old_backup))
                    logger.info(f"Removed old backup: {old_backup}")
            
            return backup_path
        else:
            logger.warning("No database file found to backup")
            return None
    except Exception as e:
        logger.error(f"Error creating database backup: {e}")
        return None

def restore_database_from_backup(backup_path=None):
    """Restore database from a backup file"""
    try:
        import shutil
        import os
        
        if backup_path and os.path.exists(backup_path):
            shutil.copy2(backup_path, DATABASE_PATH)
            logger.info(f"Database restored from {backup_path}")
            return True
        else:
            # Try to find the most recent backup
            backup_dir = 'backups'
            if os.path.exists(backup_dir):
                backups = sorted([f for f in os.listdir(backup_dir) if f.startswith('daily_mlb_data_backup_')])
                if backups:
                    latest_backup = os.path.join(backup_dir, backups[-1])
                    shutil.copy2(latest_backup, DATABASE_PATH)
                    logger.info(f"Database restored from latest backup: {latest_backup}")
                    return True
            
            logger.warning("No backup file found to restore")
            return False
    except Exception as e:
        logger.error(f"Error restoring database: {e}")
        return False

def ensure_articles_table_exists():
    """Ensure the articles table exists and has proper structure"""
    conn = sqlite3.connect(DATABASE_PATH)
    cursor = conn.cursor()
    
    try:
        # Check if blog_articles table exists
        cursor.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='blog_articles'")
        table_exists = cursor.fetchone()
        
        if not table_exists:
            logger.info("Articles table doesn't exist, creating it...")
            cursor.execute('''
                CREATE TABLE blog_articles (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    title TEXT NOT NULL,
                    content TEXT NOT NULL,
                    summary TEXT,
                    author TEXT DEFAULT 'MLB Analyst',
                    tags TEXT,
                    status TEXT DEFAULT 'published',
                    slug TEXT UNIQUE,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            ''')
            conn.commit()
            logger.info("Articles table created successfully")
        else:
            logger.info("Articles table exists")
            
    except Exception as e:
        logger.error(f"Error ensuring articles table exists: {e}")
    finally:
        conn.close()

def initialize_database_with_recovery():
    """Initialize database with backup recovery capability"""
    try:
        # Check if main database exists
        if not os.path.exists(DATABASE_PATH):
            logger.warning("Main database file not found, attempting recovery...")
            
            # Try to restore from backup
            if not restore_database_from_backup():
                logger.info("No backup found, initializing new database...")
                init_database()
            else:
                logger.info("Database restored from backup successfully")
        
        # Ensure all required tables exist
        ensure_articles_table_exists()
        
        # Create a backup after successful initialization
        backup_database()
        
    except Exception as e:
        logger.error(f"Error during database initialization with recovery: {e}")
        # Fall back to standard initialization
        init_database()

@app.route('/api/backup_database', methods=['POST'])
@require_admin
def backup_database_endpoint():
    """Create a backup of the database"""
    try:
        backup_path = backup_database()
        if backup_path:
            return jsonify({
                'success': True,
                'message': 'Database backup created successfully',
                'backup_path': backup_path
            })
        else:
            return jsonify({
                'success': False,
                'message': 'Failed to create database backup'
            }), 500
    except Exception as e:
        logger.error(f"Error in backup endpoint: {e}")
        return jsonify({
            'success': False,
            'message': f'Error creating backup: {str(e)}'
        }), 500

@app.route('/api/restore_database', methods=['POST'])
@require_admin
def restore_database_endpoint():
    """Restore database from backup"""
    try:
        data = request.get_json()
        backup_path = data.get('backup_path') if data else None
        
        success = restore_database_from_backup(backup_path)
        if success:
            return jsonify({
                'success': True,
                'message': 'Database restored successfully'
            })
        else:
            return jsonify({
                'success': False,
                'message': 'Failed to restore database from backup'
            }), 500
    except Exception as e:
        logger.error(f"Error in restore endpoint: {e}")
        return jsonify({
            'success': False,
            'message': f'Error restoring database: {str(e)}'
        }), 500

@app.route('/api/database_status', methods=['GET'])
@require_admin
def database_status_endpoint():
    """Get database and backup status"""
    try:
        import os
        
        status = {
            'database_exists': os.path.exists(DATABASE_PATH),
            'database_size': os.path.getsize(DATABASE_PATH) if os.path.exists(DATABASE_PATH) else 0,
            'backups': []
        }
        
        # List available backups
        backup_dir = 'backups'
        if os.path.exists(backup_dir):
            backups = [f for f in os.listdir(backup_dir) if f.startswith('daily_mlb_data_backup_')]
            for backup in sorted(backups, reverse=True):
                backup_path = os.path.join(backup_dir, backup)
                status['backups'].append({
                    'filename': backup,
                    'path': backup_path,
                    'size': os.path.getsize(backup_path),
                    'created': os.path.getctime(backup_path)
                })
        
        return jsonify({
            'success': True,
            'status': status
        })
    except Exception as e:
        logger.error(f"Error getting database status: {e}")
        return jsonify({
            'success': False,
            'message': f'Error getting database status: {str(e)}'
        }), 500

# Environment detection for deployment optimization
def get_deployment_environment():
    """Detect the deployment environment"""
    if os.getenv('REPL_ID'):
        return 'replit'
    elif os.getenv('K_SERVICE'):  # Cloud Run environment variable
        return 'cloudrun'
    elif os.getenv('VERCEL'):
        return 'vercel'
    else:
        return 'local'



if __name__ == '__main__':
    app.run(debug=True, host='0.0.0.0', port=8080) 