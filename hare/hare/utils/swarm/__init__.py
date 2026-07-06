"""
Swarm/team coordination utilities.

Port of: src/utils/swarm/
"""

from hare.utils.swarm.constants import TEAM_LEAD_NAME
from hare.utils.swarm.team_helpers import (
    sanitize_name,
    get_team_file_path,
    read_team_file,
    write_team_file,
)
from hare.utils.swarm.teammate_layout import (
    assign_teammate_color,
    clear_teammate_colors,
)
