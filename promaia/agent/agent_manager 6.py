"""
Agent session management for Promaia agent orchestration.

Handles spawning, communicating with, and managing Claude Code agent
subprocesses for action-based tasks.
"""

import asyncio
import logging
from typing import Dict, List, Any, Optional, AsyncIterator
from dataclasses import dataclass
from datetime import datetime

from claude_agent_sdk import ClaudeSDKClient, ClaudeAgentOptions

try:
    from .context_serializer import (
        serialize_context_for_agent,
        create_agent_system_prompt,
        format_context_summary
    )
    from .sdk_adapter_simple import load_mcp_servers_from_claude_config
except ImportError:
    # Running as script - use absolute imports
    from promaia.agent.context_serializer import (
        serialize_context_for_agent,
        create_agent_system_prompt,
        format_context_summary
    )
    from promaia.agent.sdk_adapter_simple import load_mcp_servers_from_claude_config

logger = logging.getLogger(__name__)


@dataclass
class AgentMessage:
    """Represents a message from the agent."""
    role: str  # "assistant", "tool_use", "tool_result"
    content: str
    timestamp: datetime
    metadata: Optional[Dict[str, Any]] = None


class AgentSession:
    """
    Manages a single Claude Code agent session.

    Handles the lifecycle of an agent subprocess:
    - Spawning with context
    - Sending messages
    - Receiving responses
    - Cleanup
    """

    def __init__(
        self,
        task: str,
        context: Dict[str, List[Dict[str, Any]]],
        workspace: Optional[str] = None,
        model: str = "claude-sonnet-4-6"
    ):
        """
        Initialize agent session.

        Args:
            task: The action task for the agent
            context: Promaia's loaded content
            workspace: User's workspace
            model: Claude model to use
        """
        self.task = task
        self.context = context
        self.workspace = workspace
        self.model = model
        self.client: Optional[ClaudeSDKClient] = None
        self.active = False
        self.start_time: Optional[datetime] = None

    async def spawn(self) -> bool:
        """
        Spawn the Claude Code agent subprocess.

        Returns:
            True if successful, False otherwise
        """
        try:
            logger.info(f"Spawning agent for task: {self.task}")

            # Serialize context
            context_markdown = serialize_context_for_agent(self.context)
            system_prompt = create_agent_system_prompt(
                context_markdown,
                self.task,
                self.workspace
            )

            # Load MCP servers
            mcp_servers = load_mcp_servers_from_claude_config()
            logger.info(f"Loaded {len(mcp_servers)} MCP servers")

            # Create agent options
            options = ClaudeAgentOptions(
                model=self.model,
                system_prompt=system_prompt,
                mcp_servers=mcp_servers if mcp_servers else {}
            )

            # Create and connect client
            self.client = ClaudeSDKClient(options=options)
            await self.client.connect()

            # Send the initial task query
            await self.client.query(self.task)

            self.active = True
            self.start_time = datetime.now()

            logger.info("Agent spawned successfully and task sent")
            return True

        except Exception as e:
            logger.error(f"Error spawning agent: {e}", exc_info=True)
            return False

    async def send_message(self, message: str):
        """
        Send a message to the agent.

        Args:
            message: Message to send
        """
        if not self.active or not self.client:
            raise RuntimeError("Agent not active. Call spawn() first.")

        try:
            await self.client.query(message)
        except Exception as e:
            logger.error(f"Error sending message to agent: {e}", exc_info=True)
            raise

    async def receive_messages(self) -> AsyncIterator[AgentMessage]:
        """
        Receive messages from the agent.

        Yields:
            AgentMessage objects as they arrive
        """
        if not self.active or not self.client:
            raise RuntimeError("Agent not active. Call spawn() first.")

        try:
            async for message in self.client.receive_messages():
                # Parse message and convert to AgentMessage
                agent_msg = self._parse_sdk_message(message)
                if agent_msg:
                    yield agent_msg
        except Exception as e:
            logger.error(f"Error receiving messages from agent: {e}", exc_info=True)
            raise

    def _parse_sdk_message(self, message) -> Optional[AgentMessage]:
        """Parse SDK message into AgentMessage format."""
        try:
            # Handle different message types from SDK
            if hasattr(message, 'role'):
                role = message.role

                # Extract content
                content = ""
                if hasattr(message, 'content'):
                    if isinstance(message.content, list):
                        for block in message.content:
                            if hasattr(block, 'text'):
                                content += block.text
                            elif hasattr(block, 'type') and block.type == 'tool_use':
                                # Tool use block
                                return AgentMessage(
                                    role="tool_use",
                                    content=f"Using tool: {block.name}",
                                    timestamp=datetime.now(),
                                    metadata={'tool_name': block.name, 'tool_input': getattr(block, 'input', {})}
                                )
                    else:
                        content = str(message.content)

                if content:
                    return AgentMessage(
                        role=role,
                        content=content,
                        timestamp=datetime.now()
                    )

            return None

        except Exception as e:
            logger.error(f"Error parsing SDK message: {e}", exc_info=True)
            return None

    async def terminate(self):
        """Clean up agent session."""
        if self.client:
            try:
                await self.client.disconnect()
            except Exception as e:
                logger.error(f"Error disconnecting agent: {e}")

        self.active = False
        logger.info("Agent session terminated")


class AgentOrchestrator:
    """
    Orchestrates conversations between user, Promaia, and agents.

    Manages the group chat mode where user can interact with both
    Promaia and the Claude Code agent.
    """

    def __init__(self):
        """Initialize the orchestrator."""
        self.current_agent: Optional[AgentSession] = None
        self.group_chat_mode = False
        self.message_history: List[Dict[str, Any]] = []

    async def handle_action_request(
        self,
        query: str,
        context: Dict[str, List[Dict[str, Any]]],
        workspace: Optional[str] = None
    ) -> AsyncIterator[Dict[str, Any]]:
        """
        Handle an action request by spawning an agent and entering group chat mode.

        Args:
            query: User's action request
            context: Promaia's loaded content
            workspace: User's workspace

        Yields:
            Messages from the conversation
        """
        try:
            # Notify user
            yield {
                'type': 'system',
                'message': f"Action request detected. Spawning Claude Code agent...",
                'context_summary': format_context_summary(context)
            }

            # Create and spawn agent
            self.current_agent = AgentSession(
                task=query,
                context=context,
                workspace=workspace
            )

            success = await self.current_agent.spawn()
            if not success:
                yield {
                    'type': 'error',
                    'message': "Failed to spawn agent. Please try again."
                }
                return

            yield {
                'type': 'system',
                'message': "Agent spawned successfully. Entering group chat mode..."
            }

            # Enter group chat mode
            self.group_chat_mode = True

            # Send initial task to agent
            await self.current_agent.send_message(query)

            # Stream agent responses
            async for agent_message in self.current_agent.receive_messages():
                yield {
                    'type': 'agent',
                    'role': agent_message.role,
                    'content': agent_message.content,
                    'metadata': agent_message.metadata
                }

                # Check if agent is done
                if self._is_task_complete(agent_message):
                    yield {
                        'type': 'system',
                        'message': "Task complete. Exiting group chat mode."
                    }
                    break

        except Exception as e:
            logger.error(f"Error in action request handling: {e}", exc_info=True)
            yield {
                'type': 'error',
                'message': f"Error: {str(e)}"
            }

        finally:
            # Cleanup
            await self.cleanup()

    async def send_user_message(self, message: str, target: str = "agent") -> bool:
        """
        Send a user message during group chat.

        Args:
            message: User's message
            target: "agent" or "promaia"

        Returns:
            True if sent successfully
        """
        if target == "agent" and self.current_agent and self.current_agent.active:
            try:
                await self.current_agent.send_message(message)
                return True
            except Exception as e:
                logger.error(f"Error sending user message to agent: {e}")
                return False
        elif target == "promaia":
            # TODO: Route to Promaia's existing chat handler
            logger.info(f"User message to Promaia: {message}")
            return True

        return False

    def _is_task_complete(self, message: AgentMessage) -> bool:
        """
        Determine if the agent has completed the task.

        This is a heuristic - looks for completion indicators.
        """
        if message.role != "assistant":
            return False

        # Look for completion phrases
        completion_phrases = [
            "task complete",
            "done",
            "finished",
            "sent successfully",
            "created successfully",
            "updated successfully"
        ]

        content_lower = message.content.lower()
        return any(phrase in content_lower for phrase in completion_phrases)

    async def cleanup(self):
        """Clean up agent session and exit group chat mode."""
        if self.current_agent:
            await self.current_agent.terminate()
            self.current_agent = None

        self.group_chat_mode = False
        logger.info("Agent orchestrator cleaned up")

    def is_in_group_chat(self) -> bool:
        """Check if currently in group chat mode."""
        return self.group_chat_mode

    def get_agent_status(self) -> Dict[str, Any]:
        """Get current agent status."""
        if not self.current_agent:
            return {'active': False}

        return {
            'active': self.current_agent.active,
            'task': self.current_agent.task,
            'workspace': self.current_agent.workspace,
            'start_time': self.current_agent.start_time.isoformat() if self.current_agent.start_time else None
        }


# For testing
if __name__ == "__main__":
    import asyncio
    import sys
    from pathlib import Path

    # Add parent directory to path for imports
    sys.path.insert(0, str(Path(__file__).parent.parent.parent))

    from promaia.agent.context_serializer import (
        serialize_context_for_agent,
        create_agent_system_prompt
    )
    from promaia.agent.sdk_adapter_simple import load_mcp_servers_from_claude_config

    async def test_agent_spawn():
        """Test spawning an agent with sample context."""
        print("Testing Agent Spawner")
        print("=" * 60)

        # Sample context
        test_context = {
            "koii.journal": [
                {
                    "title": "2024-01-15 - Monday",
                    "content": "Worked on API endpoints today.",
                    "created_time": "2024-01-15T09:00:00Z"
                }
            ]
        }

        # Create session
        session = AgentSession(
            task="Tell me what you can see in the context",
            context=test_context,
            workspace="koii"
        )

        # Spawn agent
        print("\nSpawning agent...")
        success = await session.spawn()

        if success:
            print("✓ Agent spawned successfully")

            # Try to receive one message
            print("\nWaiting for agent response...")
            try:
                async for message in session.receive_messages():
                    print(f"\n[{message.role}]: {message.content}")
                    break  # Just get first message for test
            except Exception as e:
                print(f"Error: {e}")

            # Cleanup
            await session.terminate()
        else:
            print("✗ Failed to spawn agent")

    # Run test
    asyncio.run(test_agent_spawn())
