"""Compatibility entry point for the hosted agent CLI."""

from agents.hosted.agent import AgentComponents, build_agent, main, run_agent


__all__ = ["AgentComponents", "build_agent", "main", "run_agent"]


if __name__ == "__main__":
    main()
