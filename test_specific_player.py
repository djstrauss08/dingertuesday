import statsapi
import json
from pprint import pprint

# Helper function to get player stats
def get_player_stats(player_id, group="hitting", type="season"):
    # If not in cache, fetch from API
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
    
    return parsed_stats

# Pete Alonso's player ID
player_id = 624413  # Pete Alonso

print("Testing available stats for Pete Alonso that could replace advanced metrics...")

# Get regular season stats
season_stats = get_player_stats(player_id, group="hitting", type="season")
print("\n=== USEFUL REGULAR SEASON STATS ===")
useful_stats = [
    'homeRuns', 'hits', 'atBats', 'avg', 'obp', 'slg', 'ops', 
    'doubles', 'triples', 'rbi', 'runs', 'stolenBases', 
    'strikeOuts', 'baseOnBalls'
]
for stat in useful_stats:
    print(f"{stat}: {season_stats.get(stat, 'N/A')}")

# Get advanced stats
advanced_stats = get_player_stats(player_id, group="hitting", type="seasonAdvanced")
print("\n=== USEFUL ADVANCED STATS ===")
useful_advanced_stats = [
    'babip', 'extraBaseHits', 'iso', 'walksPerStrikeout', 
    'homeRunsPerPlateAppearance', 'strikeoutsPerPlateAppearance'
]
for stat in useful_advanced_stats:
    print(f"{stat}: {advanced_stats.get(stat, 'N/A')}") 