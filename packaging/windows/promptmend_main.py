"""The frozen Windows build's entry script (#185): the console script's entry point, so a
trigger call silences stderr before the CLI is imported, exactly as an installed
`promptmend.exe` does."""

from promptmend.entry import main

if __name__ == "__main__":
    main()
