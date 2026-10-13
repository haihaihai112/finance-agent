"""开发时的未实现功能异常，保留供未来扩展使用。"""


class FeatureNotImplemented(NotImplementedError):
    def __init__(self, feature: str):
        self.feature = feature
        super().__init__(f"{feature} 尚未实现。")
