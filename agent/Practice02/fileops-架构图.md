flowchart TB
    START([python fileops.py])

    subgraph L1[配置层]
        CFG[读取 config.ini]
        CLIENT[创建 OpenAI client]
        MODEL[读取 model 名称]
        CFG --> CLIENT
        CFG --> MODEL
    end

    subgraph L2[工具实现层]
        T1[getDate 调用 date 命令]
        T2[isLeapYear 取模判断]
        H1[readText 解码纯文本]
        D1[getCwd 取当前目录]
        D2[listDir 列目录]
        D3[createDir 建目录]
        D4[deleteDir 删非空目录]
        F1[getFileType 查类型]
        F2[readFile 读内容]
        F3[createFile 建文件]
        F4[editFile 覆盖写]
        F5[deleteFile 删文件]
        F6[renamePath 重命名]
        F2 --> H1
        F4 --> H1
    end

    subgraph L3[声明与分发层]
        TOOLS[TOOLS 声明 12 个 schema]
        RUN[run_tool 按名字分发]
        ARGS[json 解析参数]
        TOOLS --> RUN
        ARGS --> RUN
    end

    subgraph L4[主循环]
        IN[input 等待输入]
        SKIP{输入是否为空}
        MSG[messages 累积上下文]
        REQ[chat 请求 流式 tools]
        PARSE[解析 content 与 tool_calls]
        JUDGE{calls 是否为空}
        OUT[打印最终回答]
        BACK[追加 role tool 结果]
        IN --> SKIP
        SKIP -->|是| IN
        SKIP -->|否| MSG
        MSG --> REQ
        REQ --> PARSE
        PARSE --> JUDGE
        JUDGE -->|是| OUT
        JUDGE -->|否| BACK
        BACK --> REQ
        OUT --> IN
    end

    subgraph L5[系统资源]
        OS[Windows 命令行]
        FS[本地文件系统]
    end

    START --> CFG
    START --> IN
    CLIENT --> REQ
    MODEL --> REQ
    OUT -.->|Ctrl C 退出| END([程序结束])

    RUN --> T1
    RUN --> T2
    RUN --> D1
    RUN --> D2
    RUN --> D3
    RUN --> D4
    RUN --> F1
    RUN --> F2
    RUN --> F3
    RUN --> F4
    RUN --> F5
    RUN --> F6
    BACK --> RUN
    T1 --> OS
    D1 --> FS
    D2 --> FS
    D3 --> FS
    D4 --> FS
    F1 --> FS
    F2 --> FS
    F3 --> FS
    F4 --> FS
    F5 --> FS
    F6 --> FS
