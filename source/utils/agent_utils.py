# Standard library imports
import logging
from typing import Optional, Tuple, AsyncIterator, Union

# Third-party imports
from semantic_kernel.agents import AgentGroupChat, ChatCompletionAgent, ChatHistoryAgentThread
from semantic_kernel.contents import ChatHistory, ChatMessageContent, ImageContent, TextContent
from semantic_kernel.contents.chat_message_content import ChatMessageContent
from semantic_kernel.contents.function_call_content import FunctionCallContent
from semantic_kernel.contents.function_result_content import FunctionResultContent
from semantic_kernel.contents.utils.author_role import AuthorRole
from semantic_kernel.functions.kernel_arguments import KernelArguments
from utils.recursive_config import Config

config = Config()
logger = logging.getLogger("main")

def _write_content(content: ChatMessageContent) -> None:
    """Write the content to the console."""
    if not content.items:
        logger.debug("#DEBUG (write content): [(empty)] %s : '(no content)'", content.role)
        return
        
    last_item = content.items[-1]
    last_item_type = type(last_item).__name__
    
    message_content = ""
    if isinstance(last_item, FunctionCallContent):
        message_content = f"tool request = {last_item.function_name}"
    elif isinstance(last_item, FunctionResultContent):
        message_content = f"function result = {last_item.result}"
    else:
        message_content = str(last_item)

    logger.info("#Content: [%s] %s : '%s'", last_item_type, content.role, message_content)


async def invoke_agent(
    agent: ChatCompletionAgent,
    thread: ChatHistoryAgentThread,
    input_text_message: str,
    input_image_message: ImageContent = None,
    save_to_history: bool = True,
    arguments: KernelArguments = None
    ) -> Tuple[str, ChatHistoryAgentThread]:
    """
    Invoke the agent with the user input.
    
    Args:
        agent (ChatCompletionAgent): The agent to invoke
        input_text_message (str): Text message to send to the agent
        input_image_message (ImageContent, optional): Image content to send with the message
        thread (ChatHistoryAgentThread, optional): Chat thread to use
        save_to_history (bool): Whether to save the conversation to history
        debug (bool): Whether to print debug messages
        arguments (KernelArguments, optional): Additional arguments to pass to the agent
        
    Returns:
        tuple: (response content, updated thread)
    """
    logger.info("Agent %s Invoked.", agent.name)
    logger.debug("Exact message sent to agent: %s", input_text_message)
    
    # Create message with text and optional image
    message = None
    if input_text_message is not None:
        if input_image_message is not None:
            message = [TextContent(text=input_text_message), input_image_message]
        else:
            message = input_text_message
    
    # Save original messages if we shouldn't save to history
    if thread is not None:
        orig_chat_history = await thread.get_messages()
    else:
        orig_chat_history = None
    
    response = await agent.get_response(messages=message, thread=thread, arguments=arguments)
    logger.debug("Raw response from agent: %s", response)
    
    # Log all content items, including function calls
    chat_history = await response.thread.get_messages()
    
    # Get the messages that were added during this invocation (the new ones)
    start_idx = len(orig_chat_history) if orig_chat_history is not None else 0
    for msg in chat_history.messages[start_idx:]:
        # Log the role of each message to better understand the conversation flow
        logger.debug("Processing message with role: %s", msg.role)
        
        # Process all messages regardless of role
        for item in msg.items:
            if isinstance(item, FunctionCallContent):
                logger.info("#Function Call: %s(%s)", item.function_name, item.arguments)

    # Log the final response content
    _write_content(response.content)
    
    # If we shouldn't save to history, create a new thread with the original messages
    if not save_to_history:
        # Create a new thread with the original messages
        new_thread = ChatHistoryAgentThread(chat_history=orig_chat_history, thread_id=thread._thread_id)
        await new_thread.create()
        thread = new_thread
        
    return response.content, response.thread


async def invoke_agent_group_chat(
    group_chat: AgentGroupChat, 
    input_text_message: str, 
    input_image_message: ImageContent = None,
    save_to_history: bool = True,
    debug: bool = True,
    agent: ChatCompletionAgent = None
) -> Tuple[str, AgentGroupChat]:
    """
    Invoke the agent group chat with an input message and collect the response.
    
    Args:
        group_chat (AgentGroupChat): The group chat to invoke
        input_text_message (str): Message to send to the group chat
        input_image_message (ImageContent, optional): Image content to send with the message
        save_to_history (bool): Whether to save the message to chat history
        debug (bool): Whether to print debug messages
        agent (ChatCompletionAgent, optional): Specific agent to target in the group chat
        
    Returns:
        tuple: (response string, updated group chat)
    """
    
    if debug:
        logger.debug("# %s: '%s'", AuthorRole.USER, input_text_message)
    
    init_history = None
    if save_to_history is False:
        if hasattr(group_chat.history, 'clone'):
            init_history = group_chat.history.clone()
        else:
            init_history = group_chat.history

    # Add user message to chat
    if input_image_message is not None:
        await group_chat.add_chat_message(
            ChatMessageContent(role=AuthorRole.USER, items=[TextContent(text=input_text_message), input_image_message])
        )
    else:
        await group_chat.add_chat_message(
            ChatMessageContent(role=AuthorRole.USER, content=input_text_message)
        )
        
    response = ""
    
    # Invoke the group chat and collect responses
    async for content in group_chat.invoke(agent=agent):
        response += content.content
        if debug:
            logger.debug("### DEBUG: Group chat content added to response: %s", content.content)

    logger.debug("### DEBUG: Group chat response: %s", response)
    
    # Add the response to history
    await group_chat.add_chat_message(
        ChatMessageContent(role=AuthorRole.ASSISTANT, content=response)
    )

    if save_to_history is False and init_history is not None:
        group_chat.history = init_history

    return response, group_chat