# tools 包导出模块
# 统一导出 ImageAgent 所需的全部工具类和 CrewAI Tool 工厂函数，
# 外部只需 `from agents.image_agent.tools import xxx` 即可使用。

from .image_generator import (
    get_image_generator_tool,   # CrewAI Tool: OpenAI DALL-E 生图
    ImageGenerator,             # OpenAI 图片生成器类
    ImageStyle,                 # 业务视觉风格枚举（VisualStyle 别名）
    VisualStyle,                # 业务视觉风格枚举
    OpenAIImageStyle,           # OpenAI 原生 style 枚举（vivid/natural）
)
from .alt_text_generator import (
    get_alt_text_generator_tool,  # CrewAI Tool: Alt 文本生成
    AltTextGenerator,             # Alt 文本生成器类
)
from .coze_image_provider import (
    CozeImageProvider,          # Coze Site 图片 Provider 类
    get_coze_image_tool,        # CrewAI Tool: Coze 生图
)
from .image_prompt_generator import (
    generate_image_prompts_from_db,  # 第一阶段：DB→DeepSeek→提示词
    get_image_prompt_tool,           # CrewAI Tool: 提示词生成
    DeepSeekPromptClient,            # DeepSeek 客户端类
    PromptLLMConfig,                 # DeepSeek 配置 dataclass
)
from .prompt_to_image import (
    generate_images_from_prompts,  # 第二阶段：提示词→Coze→图片
    get_prompt_to_image_tool,      # CrewAI Tool: 提示词转图片
    PromptToImagePipeline,         # 提示词转图片流水线类
)

__all__ = [
    # CrewAI Tool 工厂函数
    "get_image_generator_tool",
    "get_alt_text_generator_tool",
    "get_coze_image_tool",
    "get_image_prompt_tool",
    "get_prompt_to_image_tool",
    # 工具类
    "ImageGenerator",
    "CozeImageProvider",
    "AltTextGenerator",
    # 枚举/类型
    "ImageStyle",
    "VisualStyle",
    "OpenAIImageStyle",
    # 流水线函数与配置类
    "generate_image_prompts_from_db",
    "DeepSeekPromptClient",
    "PromptLLMConfig",
    "generate_images_from_prompts",
    "PromptToImagePipeline",
]
