import yaml

from pathlib import Path
from typing import Optional, Tuple, List
from dotenv import dotenv_values

from semantic_kernel import Kernel
from semantic_kernel.agents import ChatCompletionAgent, AgentGroupChat
from semantic_kernel.agents.strategies.termination.termination_strategy import TerminationStrategy
from semantic_kernel.services.ai_service_client_base import AIServiceClientBase
from semantic_kernel.connectors.ai.open_ai import OpenAIChatPromptExecutionSettings, OpenAIChatCompletion
from semantic_kernel.connectors.ai.function_choice_behavior import FunctionChoiceBehavior

from semantic_kernel.contents import ChatHistory
from semantic_kernel.functions import KernelArguments

from configs.plugin_configs import plugin_configs
from configs.scenes_and_plugins_config import Scene
from source.planner_core.robot_state import RobotState
from configs.agent_instruction_prompts import TASK_EXECUTION_AGENT_INSTRUCTIONS, TASK_GENERATION_AGENT_INSTRUCTIONS, GOAL_COMPLETION_CHECKER_AGENT_INSTRUCTIONS

from source.utils.logging_utils import setup_logging
logger_plugins, logger_main = setup_logging()



        

class RobotPlanner:
    def __init__(
        self, 
        scene: Scene
    ) -> None:
        """
        Constructor for the RobotPlanner class that handles plugin initialization and planning.

        Args:
            task_execution_service (AIServiceClientBase): The AI service client (e.g. OpenAI) that will
                be used by the semantic kernel for the execution of the task that have to be completed.
            task_generation_service (AIServiceClientBase): The AI service client (e.g. OpenAI) that will
                be used by the semantic kernel for the generation of the task that have to be completed.
            task_execution_endpoint_settings (OpenAIChatPromptExecutionSettings): The settings for the request to the AI service.
            task_generation_endpoint_settings (OpenAIChatPromptExecutionSettings): The settings for the request to the AI service.
            enabled_plugins (List[str]): List of plugin names that should be enabled for the
                current scene, e.g. ["nav", "text", "sql", "image"].
            plugin_configs (dict): Configuration dictionary for plugins containing tuples of
                (factory_function, arguments, kernel_name) for each plugin.
        """
        # Load settings
        self._planner_settings = dotenv_values(".env_core_planner")

        # Set plugin configurations
        self._enabled_plugins = self.scene.plugins
        self._plugin_configs = plugin_configs
        
        self.robot_state = RobotState()
        

    def _load_config(self) -> dict:
        """
        Load configuration from config.yaml file.
        
        """
        with open(Path(self._planner_settings.get("PROJECT_DIR")) / 'configs' / 'config.yaml', 'r') as file:
            return yaml.safe_load(file)

    def add_plugins(self) -> None:
        """
        Adds all the enabled plugins to the kernel.
        Scenes_and_plugins_config.py contains the plugin configurations for each scene.
        """
        # Addd our kernel Service
        self.kernel = Kernel()
        
        # Add Enabled Plugins to the kernel
        for plugin_name in self._enabled_plugins:
            if plugin_name in self._plugin_configs:
                factory_func, args, kernel_name = self._plugin_configs[plugin_name]
                plugin = factory_func(*args)
                self.kernel.add_plugin(plugin, plugin_name=kernel_name)


    def setup_services(self) -> None:
        """
        Set up AI services with appropriate models and API keys.
        Configures both the main GPT-4 model and the auxiliary reasoning model.
        """

        # Set up highly intelligent OpenAI model for answering question
        self.kernel.add_service(OpenAIChatCompletion(
            service_id="general_intelligence",
            api_key=dotenv_values().get("OPENAI_API_KEY"),
            ai_model_id="gpt-4o-2024-11-20"))

        # # Set up highly intelligent Google Gemini model for answering question
        # self.kernel.add_service(GoogleAIChatCompletion(
        #     service_id="general_intelligence",
        #     api_key=dotenv_values().get("GEMINI_API_KEY"),
        #     gemini_model_id="gemini-2.0-flash"))

        # Set Up Reasoning Model for the analysis of the experiences to requirements matching
        self.kernel.add_service(OpenAIChatCompletion(
            service_id="small_reasoning_model",
            api_key=dotenv_values().get("OPENAI_API_KEY"),
            ai_model_id="gpt-4o-2024-11-20")) # will be replaced by o3 mini in the future!
        
        # Set Up small and cheap model for the processing of certain user responses
        self.kernel.add_service(OpenAIChatCompletion(
            service_id="small_cheap_model",
            api_key=dotenv_values().get("OPENAI_API_KEY"),
            ai_model_id="gpt-4o-mini"))


    def initialize_task_generation_agent(self) -> None:
        """
        Initializes the task generation agent.
        """

        # Create task generation agent with auto function calling
        task_generation_endpoint_settings = OpenAIChatPromptExecutionSettings(
            service_id="general_intelligence",
            max_tokens=int(self._planner_settings.get("MAX_TOKENS")),
            temperature=float(self._planner_settings.get("TEMPERATURE")),
            top_p=float(self._planner_settings.get("TOP_P")),
            function_choice_behavior=FunctionChoiceBehavior.Auto() # auto function calling
        )
        self.task_generation_agent = ChatCompletionAgent(
            service_id="general_intelligence",
            kernel=self.kernel,
            name="TaskGenerationAgent",
            instructions=self._task_generation_agent_instructions,
            execution_settings=task_generation_endpoint_settings
        )


    def initialize_task_execution_agent(self) -> None:
        """
        Initializes the task execution agent.
        """

        task_execution_endpoint_settings = OpenAIChatPromptExecutionSettings(
            service_id="general_intelligence",
            max_tokens=int(self._planner_settings.get("MAX_TOKENS")),
            temperature=float(self._planner_settings.get("TEMPERATURE")),
            top_p=float(self._planner_settings.get("TOP_P")),
            function_choice_behavior=FunctionChoiceBehavior.Auto()
        )

        # Create task execution agent with auto function calling
        self.task_execution_agent = ChatCompletionAgent(
            service_id="general_intelligence",
            kernel=self.kernel,
            name="TaskExecutionAgent",
            instructions=self._task_execution_agent_instructions,
            execution_settings=task_execution_endpoint_settings
        )

    def initialize_goal_completion_checker_agent(self) -> None:
        """
        Initializes the goal completion checker agent.
        """
        goal_completion_checker_endpoint_settings = OpenAIChatPromptExecutionSettings(
            service_id="general_intelligence",
            max_tokens=int(self._planner_settings.get("MAX_TOKENS")),
            temperature=float(self._planner_settings.get("TEMPERATURE")),
            top_p=float(self._planner_settings.get("TOP_P")),
            function_choice_behavior=FunctionChoiceBehavior.Auto()
        )
        self.goal_completion_checker_agent = ChatCompletionAgent(
            service_id="general_intelligence",
            kernel=self.kernel,
            name="GoalCompletionCheckerAgent",
            instructions=self._goal_completion_checker_agent_instructions,
            execution_settings=goal_completion_checker_endpoint_settings
        )

    def setup_agent_group_chat(self, agents:list[ChatCompletionAgent]):
        # Create chat for requirement analysis
        self.application_question_answer_group_chat = AgentGroupChat(
            agents=agents,
            termination_strategy=ApprovalTerminationStrategy(agents=[self.goal_completion_checker_agent], maximum_iterations=10)
        )
        return self.application_question_answer_group_chat


    async def invoke_task_generation_agent(self, goal: str, completed_tasks: list = None, env_state: str = None) -> Tuple:
        """
        Invokes the task generation agent to generate a list of tasks to complete based on the goal that is provided.
        
        Args:
            goal (str): The main goal to accomplish
            completed_tasks (list, optional): List of tasks already completed
            env_state (str, optional): Current state of the environment
        """
        # Create structured user message following the template
        task_generation_user_message = f"""
Goal: {goal}

Tasks Completed: 
{chr(10).join([f"- {task}" for task in completed_tasks]) if completed_tasks else "No tasks completed yet"}

Environment State:
{env_state if env_state else "Initial state - no environment data available"}
"""

        # Create chat history for this interaction
        self._goal_generator_history = ChatHistory()
        self._goal_generator_history.add_user_message(task_generation_user_message)

        # Invoke agent and get response
        async for response in self._task_generation_agent.invoke(self._goal_generator_history):
            return response.contents

    async def invoke_robot_on_task(self, task: str) -> Tuple[str, str]:
        """
        The robot achieves the given task using automatic tool calling via an agent.

        Args:
            task (str): task to be executed

        Returns:
            Tuple[str, str]: final response and chat history
        """
        if self.kernel is None:
            raise ValueError("You need to set the Semantic Kernel first")

        try:
            # Add task to chat history
            self._task_executer_history = ChatHistory()
            self._task_executer_history.add_user_message(task)

            # Invoke agent and get response with function calls
            async for response in self._task_execution_agent.invoke(self._task_executer_history):
                # Store response in history
                self._task_executer_history.add_assistant_message(response.content)
                return response.content, self._task_executer_history

        except Exception as e:
            logger_main.error(f"Error during task execution: {str(e)}")
            raise RuntimeError(f"Task execution failed: {str(e)}")

    async def invoke_goal_completion_checker_agent(self, goal: str, completed_tasks: list = None, env_state: str = None) -> Tuple:
        """
        Invokes the goal completion checker agent to check if the goal has been completed.
        The goal completion checker agent usually only gets activated 1 or 2 steps before the task generation agent plans the task to be completed.
        """
        pass

    # Check out: get access/insight on the plan that was made (e.g. telemetry support)


class ApprovalTerminationStrategy(TerminationStrategy):
    """A strategy for determining when an agent should terminate."""

    async def should_agent_terminate(self, agent, history):
        """Check if the agent should terminate."""
        return "Approved! The goal is completed!" in history[-1].content.lower()