# Standard library imports
import logging
from pathlib import Path
import tempfile
import time
import numpy as np
from dataclasses import dataclass, field
from typing import Optional, Union
from PIL import Image

# Third-party imports
from dotenv import dotenv_values

# Bosdyn SDK imports
from bosdyn import client as bosdyn_client
from bosdyn.api import estop_pb2
from bosdyn.api.spot import robot_command_pb2 as spot_command_pb2
from bosdyn.client import math_helpers, util as bosdyn_util
from bosdyn.client.estop import EstopClient
from bosdyn.client.robot_command import (
    RobotCommandClient,
    blocking_selfright,
    blocking_stand,
)

from semantic_kernel.contents import ImageContent

# Utils
from utils.recursive_config import Config

# Import singletons
from utils.singletons import (
    _SingletonWrapper,
    GraphNavClientSingleton,
    ImageClientSingleton,
    RobotCommandClientSingleton,
    RobotSingleton,
    RobotStateClientSingleton,
    RobotLeaseClientSingleton,
    WorldObjectClientSingleton,
)

# Initialize singleton instances
graph_nav_client = GraphNavClientSingleton()
image_client = ImageClientSingleton()
robot_command_client = RobotCommandClientSingleton()
robot = RobotSingleton()
robot_state_client = RobotStateClientSingleton()
robot_lease_client = RobotLeaseClientSingleton()
world_object_client = WorldObjectClientSingleton()

# Import the scene graph class
from LostFound.src.scene_graph import SceneGraph

# Set up logger
logger = logging.getLogger("main")

@dataclass
class RobotState:
    """
    Represents the current state of the Spot robot, serving as short-term memory for the agentic framework.
    
    This class maintains real-time information about the robot's state, including:
    - Live video feed from the front camera
    - Semantic scene graph with objects in field of view highlighted
    - Current room location
    - History of recent actions
    - Current task
    
    It provides methods to update this state and format it for use by the agentic framework.
    """
    config = Config()
    use_robot = config["robot_planner_settings"]["use_with_robot"]
    # objects_in_view: List[int] = field(default_factory=list)
    
    
    def __init__(self, scene_graph_object: Optional[SceneGraph] = None, scene_graph_str: Optional[str] = None):

        # Image state
        if self.use_robot:
            self._get_available_cameras()
        self.default_image_source = "frontleft_fisheye_image"
        self.hand_image_sources = ['hand_color_image', 'hand_color_in_hand_depth_frame', 'hand_depth', 'hand_depth_in_hand_color_frame', 'hand_image']

        # Initialize image and depth image states
        self.image_state = None
        self.depth_image_state = None

        # # The image state gets updated in the main function
        # self.save_image_state(image_description="initial_image")
        
        if scene_graph_object is not None:
            self.scene_graph = scene_graph_object  # Explicitly set the scene_graph attribute
        else:
            self.scene_graph = None
        
        self.current_room: str = "unknown" # Can be loaded from the scene configuration file
        
        # Gripper states
        self.object_in_gripper = None
        
        # Variables for in simulation
        self.virtual_robot_pose = np.array([1, 0, 0.6])
        
        # Core memory
        self.core_memory = dict()
        
    def set_image_state(self, image: np.ndarray) -> None:
        """Set the current image state."""
        self.image_state = image
    
    def set_depth_image_state(self, depth_image: np.ndarray) -> None:
        """Set the current depth image state."""
        self.depth_image_state = depth_image

    def save_image_state(self, image_description: Optional[str] = None) -> None:
        save_dir = Path(self.config["robot_planner_settings"]["path_to_scene_data"]) / self.config["robot_planner_settings"]["active_scene"] / "images"
        
        if not save_dir.exists():
            save_dir.mkdir(parents=True)
        if image_description is not None:
            save_path = save_dir / f"{time.time()}_{image_description}.png"
        else:
            save_path = save_dir / f"{time.time()}.png"

        image = Image.fromarray(self.image_state)
        image.save(save_path)

    # def save_depth_image_state(self, image_description: Optional[str] = None) -> None:
    #     save_dir = Path(self.config["robot_planner_settings"]["path_to_scene_data"]) / self.config["robot_planner_settings"]["active_scene"] / "images"
        
    #     if not save_dir.exists():
    #         save_dir.mkdir(parents=True)
    #     if image_description is not None:
    #         save_path = save_dir / f"{time.time()}_{image_description}.png"
    #     else:
    #         save_path = save_dir / f"{time.time()}.png"

    #     image = Image.fromarray(self.depth_image_state)
    #     image.save(save_path)

    def get_current_image_content(self) -> ImageContent:
        """Converts the image_state to an ImageContent instance."""

        if self.image_state is None or not isinstance(self.image_state, np.ndarray) or len(self.image_state.shape) < 2:
            logger.warning("No valid image state available.")
            return None
        
        try:
            with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as temp_file:
                temp_path = temp_file.name
                image = Image.fromarray(self.image_state)
                image.save(temp_path)
            
            image_content = ImageContent.from_image_file(temp_path)
            
            # Delete the temporary file
            Path(temp_path).unlink()
            
            return image_content
        except Exception as e:
            logger.error(f"Error converting image state to ImageContent: {e}")
            return None

    
    def _get_available_cameras(self) -> None:
        """Get a list of available cameras on the robot."""
        if not robot or not image_client:
            logger.error("Robot not connected. Cannot get available cameras.")
            return
        
        try:
            sources = image_client.list_image_sources()
            self.available_cameras = [source.name for source in sources]
            logger.info(f"Available cameras: {self.available_cameras}")
        except Exception as e:
            logger.error(f"Error getting available cameras: {e}")
            self.available_cameras = []




    # def _update_objects_in_view(self) -> None:
    #     """
    #     Update the list of objects that are currently in the robot's field of view.
        
    #     This uses the robot's current pose and camera parameters to determine which
    #     objects from the scene graph are visible in the current camera frame.
    #     """
    #     # Use the lock to ensure thread safety when updating objects in view
    #     with self._refresh_lock:
    #         if not self.scene_graph or not self.robot_pose:
    #             return
            
    #         self.objects_in_view = []
            
    #         # Camera parameters (these would be obtained from the actual robot in production)
    #         # Field of view in radians (typical values for Spot's front camera)
    #         horizontal_fov = math.radians(70)
    #         vertical_fov = math.radians(43)
            
    #         # Extract camera position and orientation from robot pose
    #         camera_position = self.robot_pose[:3, 3]
    #         camera_forward = self.robot_pose[:3, 2]  # Z-axis is forward in camera frame
    #         camera_up = self.robot_pose[:3, 1]       # Y-axis is up in camera frame
    #         camera_right = self.robot_pose[:3, 0]    # X-axis is right in camera frame
            
    #         # Check each object in the scene graph
    #         for node_id, node in self.scene_graph.nodes.items():
    #             # Get object centroid
    #             if isinstance(node, dict):  # If loaded from JSON
    #                 if 'centroid' not in node:
    #                     continue
    #                 centroid = np.array(node.get('centroid', [0, 0, 0]))
    #             else:  # If using actual SceneGraph object
    #                 if not hasattr(node, 'centroid'):
    #                     continue
    #                 centroid = node.centroid
                
    #             # Vector from camera to object
    #             direction = centroid - camera_position
    #             distance = np.linalg.norm(direction)
                
    #             # Avoid division by zero
    #             if distance < 1e-6:  # Small epsilon value
    #                 continue
                    
    #             # Normalize direction vector
    #             direction = direction / distance
                
    #             # Project direction onto camera axes
    #             forward_proj = np.dot(direction, camera_forward)
                
    #             # Only consider objects in front of the camera
    #             if forward_proj <= 0:
    #                 continue
                    
    #             # Calculate horizontal and vertical angles
    #             right_proj = np.dot(direction, camera_right)
    #             up_proj = np.dot(direction, camera_up)
                
    #             horizontal_angle = math.atan2(right_proj, forward_proj)
    #             vertical_angle = math.atan2(up_proj, forward_proj)
                
    #             # Check if object is within field of view
    #             if (abs(horizontal_angle) <= horizontal_fov / 2 and 
    #                 abs(vertical_angle) <= vertical_fov / 2):
    #                 self.objects_in_view.append(node_id)
                    
    #                 # Mark the object as in field of view in the scene graph
    #                 if isinstance(node, dict):
    #                     node['in_field_of_view'] = True
    #                 else:
    #                     # Assuming the ObjectNode class has an attribute for this
    #                     # If not, you would need to add this attribute to the class
    #                     if hasattr(node, 'in_field_of_view'):
    #                         node.in_field_of_view = True
    
    def update_room_location(self, room: str) -> None:
        """
        Update the current room location of the robot.
        
        Args:
            room: Name of the current room (e.g., "kitchen", "living room")
        """
        self.current_room = room
    

class RobotStateSingleton(_SingletonWrapper): 
    """
    Singleton for RobotState that gets used by the LSARP planning module.
    """

    _type_of_class = RobotState
