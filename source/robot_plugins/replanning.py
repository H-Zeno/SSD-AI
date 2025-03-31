import logging
import re
import json
from datetime import datetime
from typing import Annotated

from langchain.output_parsers import PydanticOutputParser
from semantic_kernel.functions.kernel_function_decorator import kernel_function
from semantic_kernel.contents import ChatMessageContent
from semantic_kernel.contents.utils.author_role import AuthorRole

from configs.agent_instruction_prompts import (
    UPDATE_TASK_PLANNER_PROMPT_TEMPLATE
)
from planner_core.robot_planner import RobotPlannerSingleton
from planner_core.robot_state import RobotStateSingleton
from configs.json_object_models import TaskPlannerResponse
from configs.goal_execution_log_models import PlanGenerationLogs
from robot_utils.frame_transformer import FrameTransformerSingleton
from utils.agent_utils import invoke_agent
from utils.recursive_config import Config


# Get singleton instances
robot_state = RobotStateSingleton()
robot_planner = RobotPlannerSingleton()
frame_transformer = FrameTransformerSingleton()

# Set up config
config = Config()
use_robot = config.get("robot_planner_settings", {}).get("use_with_robot", False)

# Set up logger
logger = logging.getLogger("plugins")

class ReplanningPlugin:
    """Plugin for replanning the task plan based on the current situation."""

    @kernel_function(description="Function to call when something happens that doesn't follow the initial plan generated by the task planning agent.")
    async def update_task_plan(self, issue_description: Annotated[str, "A detailed description of the current situation and what went different to the original plan."]) -> str:
        """Update the task plan based on issues encountered during execution."""
        parser = PydanticOutputParser(pydantic_object=TaskPlannerResponse)
        model_desc = parser.get_format_instructions()
        
        robot_planner.replanned = True
        
        planning_chat_history = await robot_planner.planning_chat_thread.get_messages()
        
        update_plan_prompt = UPDATE_TASK_PLANNER_PROMPT_TEMPLATE.format(
            goal=robot_planner.goal,
            previous_plan=robot_planner.plan,
            issue_description=issue_description, 
            tasks_completed=', '.join(map(str, robot_planner.tasks_completed)), 
            planning_chat_history=planning_chat_history, 
            scene_graph=str(robot_state.scene_graph.scene_graph_to_dict()),
            robot_position="Not available" if not use_robot else str(frame_transformer.get_current_body_position_in_frame(robot_state.frame_name)),
            model_description=model_desc
        )

        # This can technically call again the update task planner plugin (hhmm)
        updated_plan_response, robot_planner.json_format_agent_thread, agent_response_logs = await invoke_agent(
            agent=robot_planner.task_planner_agent, 
            thread=robot_planner.json_format_agent_thread,
            input_text_message=update_plan_prompt, 
            input_image_message=robot_state.get_current_image_content()
        )
        
        # logger.info("========================================")
        # logger.info(f"Reasoning about the updated plan: {str(updated_plan_response)}")
        # logger.info("========================================")

        # Convert ChatMessageContent to string
        updated_plan_response_str = str(updated_plan_response)

    
        # Define the pattern to extract everything before ```json
        pattern_before_json = r"(.*?)```json"
        match_before_json = re.search(pattern_before_json, updated_plan_response_str, re.DOTALL)

        # Extract and assign to reasoning variable
        chain_of_thought = match_before_json.group(1).strip() if match_before_json else ""

        pattern = r"```json\s*(.*?)\s*```"
        match = re.search(pattern, updated_plan_response_str, re.DOTALL)

        if match:
            json_content_inside = match.group(1)
            updated_plan_json_str = str(json_content_inside).replace('```json', '').replace('```', '').strip()
        else:
            logger.info('No ```json``` block found in response. Using the whole response as JSON.')
            updated_plan_json_str = str(updated_plan_response_str).replace('```json', '').replace('```', '').strip()
            
        try:
            logger.info("Successfully parsed JSON from updated plan generation response.")
            logger.debug("========================================")
            logger.debug(f"Updated plan JSON string: {updated_plan_json_str}")   
            logger.debug("========================================")
            
            robot_planner.plan = json.loads(updated_plan_json_str)
            robot_planner.replanning_count += 1 # log that the replanning took place
            
            agent_response_logs.plan_id = robot_planner.replanning_count
            robot_planner.task_planner_invocations.append(agent_response_logs)
            start_time = agent_response_logs.agent_invocation_start_time
            end_time = agent_response_logs.agent_invocation_end_time
            
            
            robot_planner.plan_generation_logs.append(
                PlanGenerationLogs(
                    plan_id=robot_planner.replanning_count,
                    plan=robot_planner.plan,
                    plan_generation_start_time=start_time,
                    plan_generation_end_time=end_time,
                    plan_generation_duration_seconds=(end_time - start_time).total_seconds(),
                    issue_description=issue_description,
                    chain_of_thought=chain_of_thought
                ))
            
        except json.JSONDecodeError as e:
            logger.error(f"Failed to parse JSON from response: {e}")
            await robot_planner.update_task_plan("Failed to parse JSON from response with error: " + str(e) + ". Please try again.")
        
        await robot_planner.planning_chat_thread.on_new_message(ChatMessageContent(role=AuthorRole.USER, content="Issue description with previous plan:" + issue_description))
        await robot_planner.planning_chat_thread.on_new_message(ChatMessageContent(role=AuthorRole.ASSISTANT, content="Updated plan:" + str(robot_planner.plan)))
        
        logger.info("========================================")
        logger.info(f"Extracted updated plan: {json.dumps(robot_planner.plan, indent=2)}")
        logger.info("========================================")
        robot_planner.json_format_agent_thread = None
        
        return chain_of_thought