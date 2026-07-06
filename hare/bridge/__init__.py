"""Bridge module – IDE/extension communication. Port of: src/bridge/"""

from hare.bridge.types import BridgeConfig, WorkData, WorkResponse, WorkSecret
from hare.bridge.bridge_api import BridgeApiClient, create_bridge_api_client
from hare.bridge.bridge_main import run_bridge_loop
