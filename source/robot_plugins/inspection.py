from __future__ import annotations

# =============================================================================
# Standard Library Imports
import logging
from typing import Annotated
from pathlib import Path
from PIL import Image
import numpy as np
import base64
import io
# =============================================================================
# Robot Utilities
from robot_utils.basic_movements import (stow_arm, gaze, carry)
from robot_utils.video import (get_camera_rgbd, set_gripper_camera_params)
from robot_utils.base_LSARP import ControlFunction , take_control_with_function
from robot_utils.frame_transformer import FrameTransformerSingleton

# Robot Plugins
from robot_plugins.user_communication import CommunicationPlugin

# Utils
from utils.light_switch_interaction import LightSwitchDetection
from utils.recursive_config import Config
from utils.coordinates import Pose3D

from planner_core.robot_state import RobotStateSingleton

light_switch_detection = LightSwitchDetection()
communication = CommunicationPlugin()

# =============================================================================
# Singletons

frame_transformer = FrameTransformerSingleton()
robot_state = RobotStateSingleton()

# =============================================================================
# Semantic Kernel
from semantic_kernel.functions.kernel_function_decorator import kernel_function
from semantic_kernel.connectors.ai.google.google_ai import GoogleAIChatCompletion, GoogleAIChatPromptExecutionSettings
from semantic_kernel.contents.chat_message_content import ChatMessageContent
from semantic_kernel.contents.utils.author_role import AuthorRole
from semantic_kernel.contents import TextContent, ImageContent
from semantic_kernel.contents.chat_history import ChatHistory
from dotenv import dotenv_values
from semantic_kernel.functions.kernel_arguments import KernelArguments

# =============================================================================
# Plugins

general_config = Config()
object_interaction_config = Config("object_interaction_configs")
logger = logging.getLogger("plugins")

use_robot = general_config["robot_planner_settings"]["use_with_robot"]

inspection_service = GoogleAIChatCompletion(
    gemini_model_id="gemini-2.0-flash",
    api_key=dotenv_values(".env_core_planner").get("GOOGLE_API_KEY"),
)


class InspectionPlugin:
    """This plugin contains functions to inspect certain objects in the scene."""
    
    class _Inspect_Object_With_Gaze(ControlFunction):
        def __init__(self):
            super().__init__()
            # self.color_image = None
            # self.depth_image = None

        def __call__(
            self,
            config: Config,
            object_id: int,
            object_centroid_pose: Pose3D,
            *args,
            **kwargs,
        ) -> bool:  # Return success/failure flag instead of the actual images
            try:
                logger.info("Starting object inspection with gaze")
                
                set_gripper_camera_params('1920x1080')
                carry()
                gaze(object_centroid_pose, robot_state.frame_name, gripper_open=True)
                
                # Get images
                rgbd_response = get_camera_rgbd(
                    in_frame="image",
                    vis_block=False,
                    cut_to_size=False,
                )
                
                # Validate response
                if not rgbd_response or len(rgbd_response) < 2:
                    logger.error(f"Invalid RGBD response: got {len(rgbd_response) if rgbd_response else 0} items")
                    return False
                
                # Unpack the response
                depth_tuple = rgbd_response[0]
                color_tuple = rgbd_response[1]
                
                # Validate tuples
                if len(depth_tuple) != 2 or len(color_tuple) != 2:
                    logger.error("Invalid response format from camera")
                    return False
                    
                depth_image, depth_response = depth_tuple
                color_image, color_response = color_tuple
                
                # Validate images
                if depth_image is None or color_image is None:
                    logger.error("Received None for depth or color image")
                    return False
                
                stow_arm()
                logging.info("Successfully captured images")
                logging.info(f"Captured images: color_shape={color_image.shape}, depth_shape={depth_image.shape}")
                
                # Store in robot state
                robot_state.set_image_state(color_image)
                robot_state.set_depth_image_state(depth_image)

                if robot_state.image_state is None or robot_state.depth_image_state is None:
                    logger.error("Failed to save images to robot state")
                    return False
                
                robot_state.save_image_state(f"inspection_object_{object_id}")
                return True  # Return success flag
                
            except Exception as e:
                logger.error(f"Error in _Inspect_Object_With_Gaze: {str(e)}")
                stow_arm()  # Always try to return to a safe position
                return False


    @kernel_function(description="After having navigated to an object/furniture, you can call this function to inspect the object with gaze and save the image to your memory.")
    async def inspect_object_with_gaze(self, object_id: Annotated[int, "ID of the object in the scene graph"]) -> None:
        
        # Check if object exists in scene graph
        if object_id not in robot_state.scene_graph.nodes:
            feedback = f"Tried to inspect object with ID {object_id} but it was not found in the scene graph."
            return feedback

        if not use_robot:
            object_node = robot_state.scene_graph.nodes[object_id]
            sem_label = robot_state.scene_graph.label_mapping.get(object_node.sem_label, "object")
            object_image = None

            # Load the object image for objects that have an image available (only for simulation)
            # Image paths are based on the object ID and semantic label
            image_dir = Path(general_config["robot_planner_settings"]["path_to_scene_data"]) / general_config["robot_planner_settings"]["active_scene"] / "images"
            
            # Define image mapping for different objects
            image_mapping = {
                6: "shelf_6.jpeg",
                11: "water_pitcher_11.jpeg",
                13: "shelf_13.jpeg",
                17: "picture_17.jpeg",
                19: "bottle_19.jpeg",
                20: "shelf_20.jpeg",
                22: "lamp_22.jpeg",
                24: "potted_plant_24.jpeg",
                25: None,  # Special handling for drawers
                26: None,  # Special handling for drawers
                27: None,  # Special handling for drawers
                28: "light_switch_28.jpeg",
                29: "light_switch_29.jpeg",
                30: "light_switch_30.jpeg",
                31: "light_switch_31.jpeg"
            }
            
            # Special handling for drawers (they have multiple states)
            if object_id == 25:
                # Check the is_open attribute of the drawer
                if hasattr(object_node, 'is_open') and object_node.is_open:
                    image_path = image_dir / "drawer_25_open.jpeg"
                else:
                    image_path = image_dir / "drawers_25_26_closed.jpeg"
            elif object_id == 26:
                if hasattr(object_node, 'is_open') and object_node.is_open:
                    image_path = image_dir / "drawer_26_open.jpeg"
                else:
                    image_path = image_dir / "drawers_25_26_closed.jpeg"
            elif object_id == 27:
                if hasattr(object_node, 'is_open') and object_node.is_open:
                    image_path = image_dir / "drawer_27_open.jpeg"
                else:
                    image_path = image_dir / "drawer_27_closed.jpeg"
            # For other objects with defined mappings
            elif object_id in image_mapping and image_mapping[object_id] is not None:
                image_path = image_dir / image_mapping[object_id]
            else:
                # No image available for this object
                logger.warning(f"No image available for object with ID {object_id} and semantic label {sem_label}")
                
                # Check if interactions_with_object exists before appending
                if not hasattr(object_node, 'interactions_with_object'):
                    object_node.interactions_with_object = []
                object_node.interactions_with_object.append("inspected")  # Log interaction anyway
                
                feedback = f"Inspected object with id {object_id} and semantic label {sem_label}. No image available."
                return feedback
            
            # Load the image if the file exists
            if image_path.exists():
                try:
                    # Open image file with PIL and convert to numpy array
                    pil_image = Image.open(image_path)
                    object_image = np.array(pil_image)
                    logger.info(f"Loaded image for object {object_id} from {image_path}")
                    
                except Exception as e:
                    logger.error(f"Error loading image for object {object_id}: {e}")
                    object_image = None
            else:
                logger.warning(f"Image file {image_path} does not exist")
            
            observation = None
            observation_prompt = None
            # Set the image state if an image was successfully loaded
            if object_image is not None:
                # Convert numpy array to base64 encoded data URI
                pil_img = Image.fromarray(object_image)
                buffered = io.BytesIO()
                pil_img.save(buffered, format="JPEG")
                img_str = base64.b64encode(buffered.getvalue()).decode()
                data_uri = f"data:image/jpeg;base64,{img_str}"
                
                # Create image content with data_uri
                input_image_message = ImageContent(
                    data_uri=data_uri,
                    mime_type="image/jpeg"
                )

                # TODO: Implementation of drawer affordance detection in simulation
                if sem_label == "drawer":
                    
                    if object_node.is_open:
                        observation_prompt = ChatMessageContent(
                            role=AuthorRole.USER, 
                            items=[TextContent(text="What is in the drawer?"), input_image_message]
                        )
                    else:
                        observation = "The drawer is closed."

                # TODO: Implementation of light switch affordance detection in simulation
                # # Special handling for light switches (optional)
                # if sem_label == "light switch":
                #     object_node.affordance_dict = light_switch_detection.light_switch_affordance_detection(
                #         object_node.centroid, 
                #         robot_state.image_state, 
                #         object_interaction_config["AFFORDANCE_DICT_LIGHT_SWITCHES"], 
                #         general_config["OPENAI_API_KEY"]
                #     )

                # general inspection of object

                else:
                    # Create a multimodal message with both text and image
                    observation_prompt = ChatMessageContent(
                        role=AuthorRole.USER, 
                        items=[TextContent(text="Describe concisely what you see."), input_image_message]
                    )
                
                if observation is None and observation_prompt is not None:
                    # Create a chat history and add our message
                    chat_history = ChatHistory()
                    chat_history.add_message(observation_prompt)
                    
                    # Get the response using the service
                    execution_settings = GoogleAIChatPromptExecutionSettings()
                    observation = await inspection_service.get_chat_message_content(
                        chat_history=chat_history,
                        settings=execution_settings
                    )
                
            # Log interaction
            if not hasattr(object_node, 'interactions_with_object'):
                object_node.interactions_with_object = []
            
            if observation is not None:
                object_node.interactions_with_object.append("Inspected, observation: " + str(observation))
            else:
                object_node.interactions_with_object.append("Inspected")
            
            feedback = f"Inspected object with id {object_id} and semantic label {sem_label}. Observation: {str(observation)}"
            return feedback

        centroid_pose = Pose3D(robot_state.scene_graph.nodes[object_id].centroid)
        response = await communication.ask_user(f"The robot would like to inspect object with id {object_id} and centroid {centroid_pose} with a gaze. Do you want to proceed? Please enter exactly 'yes' if you want to proceed.")   
        
        if response == "yes":

            # Create an instance we can reference after execution
            inspection_func = self._Inspect_Object_With_Gaze()
            
            # Call function and get success/failure flag
            logger.info("Calling take_control_with_function")
            take_control_with_function(
                function=inspection_func, 
                config=general_config, 
                object_id=object_id,
                object_centroid_pose=centroid_pose
            )
            logger.info(f"Completed inspecting of object with id {object_id} and centroid {centroid_pose} successfully (including saving to robot state).")
            object_node = robot_state.scene_graph.nodes[object_id]

            # Log interaction
            if not hasattr(object_node, 'interactions_with_object'):
                object_node.interactions_with_object = []
            object_node.interactions_with_object.append("inspected") 
            
            sem_label = robot_state.scene_graph.label_mapping.get(object_node.sem_label, "light switch")

            # TODO: check implementation of light switch inspection (valuable for on the real robot )
            if sem_label == "light switch":
                object_node.affordance_dict = light_switch_detection.light_switch_affordance_detection(
                    object_node.centroid, 
                    robot_state.image_state, 
                    object_interaction_config["AFFORDANCE_DICT_LIGHT_SWITCHES"], 
                    general_config["OPENAI_API_KEY"]
                )

            logger.info(f"Object inspection (of {sem_label}) logged in the scene graph.")
        
        else:
            await communication.inform_user("I will not inspect the object.")
            
        return None





