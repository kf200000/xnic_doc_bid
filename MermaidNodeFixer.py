import re

class MermaidNodeFixer:
    """
    强制修复 Mermaid 图中的圆括号：
      - 节点的外层形状括号 ( ) [ ] { } 被保留（标识符与括号必须紧挨，或行首/箭头后）
      - 节点内部的圆括号 (xxx) 转换为 \nxxx
      - 普通文本（如子图内的纯文本）中的圆括号直接转换为 \nxxx
    用法: MermaidNodeFixer.fix(mermaid_code)
    """

    @staticmethod
    def _replace_deepest(text: str) -> str:
        """递归替换最内层无嵌套的圆括号 (xxx) → \nxxx"""
        pattern = re.compile(r'\(([^()]+)\)')
        while True:
            new_text = pattern.sub(lambda m: '\n' + m.group(1), text)
            if new_text == text:
                break
            text = new_text
        return text

    @staticmethod
    def _find_matching_end(s: str, start: int) -> int:
        """从 start 位置（必须是 '(', '[', '{'）找到匹配的结束括号索引，未找到返回 -1"""
        if start >= len(s):
            return -1
        opening = s[start]
        closing = { '(': ')', '[': ']', '{': '}' }.get(opening)
        if not closing:
            return -1
        stack = []
        for i in range(start, len(s)):
            ch = s[i]
            if ch in '([{':
                stack.append(ch)
            elif ch in ')]}':
                if not stack:
                    continue
                top = stack.pop()
                if (top == '(' and ch == ')') or (top == '[' and ch == ']') or (top == '{' and ch == '}'):
                    if not stack:
                        return i
                else:
                    return -1
        return -1

    @classmethod
    def _is_node_shape_start(cls, line: str, pos: int) -> bool:
        """
        判断 line[pos] 是否是一个 Mermaid 节点的形状括号起始。
        规则（满足其一即可）：
          1. 括号在行首（匿名节点）
          2. 括号前一个字符（紧挨着）是标识符字符（字母/数字/下划线）→ 带标识符的节点
          3. 括号前面（忽略空格）是箭头（->, -->, -.-> 等）或连接符（&, |, ,）
        """
        if pos >= len(line):
            return False
        ch = line[pos]
        if ch not in '([{':
            return False

        # 规则1：行首
        if pos == 0:
            return True

        # 规则2：紧挨着的前一个字符是标识符
        if line[pos-1].isalnum() or line[pos-1] == '_':
            return True

        # 规则3：前面（忽略空格）是箭头或连接符
        i = pos - 1
        while i >= 0 and line[i] == ' ':
            i -= 1
        if i >= 0 and line[i] in '->&|,':
            return True

        return False

    @classmethod
    def _process_line(cls, line: str) -> str:
        """处理单行：保护节点形状括号内的内容，其他括号全部转换"""
        stripped = line.strip()
        if stripped.startswith('subgraph'):
            return line  # 保留 subgraph 定义行

        # 1. 找出所有节点形状括号的区间（包括标识符）
        intervals = []
        i = 0
        n = len(line)
        while i < n:
            if line[i] in '([{' and cls._is_node_shape_start(line, i):
                end = cls._find_matching_end(line, i)
                if end != -1:
                    # 向前扩展至标识符起始（如果存在）
                    start = i
                    # 如果前一个字符是标识符，则向前找到标识符的开始
                    if i > 0 and (line[i-1].isalnum() or line[i-1] == '_'):
                        j = i - 1
                        while j >= 0 and (line[j].isalnum() or line[j] == '_'):
                            j -= 1
                        start = j + 1
                    intervals.append((start, end))
                    i = end + 1
                    continue
            i += 1

        # 2. 如果没有节点区间，整行直接转换所有括号
        if not intervals:
            return cls._replace_deepest(line)

        # 3. 分段处理：节点区间保留形状括号但转换内部，区间外的普通文本转换所有括号
        intervals.sort(key=lambda x: x[0])
        parts = []
        last_end = 0
        for start, end in intervals:
            # 节点前的普通文本
            if last_end < start:
                plain = line[last_end:start]
                parts.append(cls._replace_deepest(plain))
            # 节点块
            block = line[start:end+1]
            # 找到块内第一个形状括号的位置
            shape_pos = None
            for idx, ch in enumerate(block):
                if ch in '([{':
                    shape_pos = idx
                    break
            if shape_pos is None:
                parts.append(block)  # 不应发生
            else:
                prefix = block[:shape_pos]          # 标识符部分（可能为空）
                open_ch = block[shape_pos]          # '(' 或 '[' 或 '{'
                inner = block[shape_pos+1:-1]       # 形状内部内容
                transformed_inner = cls._replace_deepest(inner)
                parts.append(prefix + open_ch + transformed_inner + block[-1])
            last_end = end + 1
        # 最后剩余的普通文本
        if last_end < n:
            parts.append(cls._replace_deepest(line[last_end:]))
        return ''.join(parts)

    @classmethod
    def fix(cls, mermaid_code: str) -> str:
        """修复整个 Mermaid 代码块中的所有括号"""
        lines = mermaid_code.splitlines()
        fixed_lines = [cls._process_line(line) for line in lines]
        return '\n'.join(fixed_lines)


# ========== 测试 ==========
if __name__ == "__main__":
    sample = """graph LR
    A[开发环境] --> B(测试环境)
    B --> C(预发布环境)
    C --> D(生产环境)

    subgraph 基础设施
        E[Kubernetes Cluster]
        F[Service Mesh (Istio/Linkerd)]
        G[API Gateway (Kong)]
        H[Message Queue (Kafka)]
        I[Monitoring & Logging (Prometheus, Grafana, ELK)]
        AWS/Azure/GCP
        CDN (七牛)
        Prometheus/Grafana
    end

    A & B & C --> E
    E --> G
    E --> H
    E --> I
    G --> D
    H --> D
    I --> D"""

    fixed = MermaidNodeFixer.fix(sample)
    print("=== 修复后代码 ===")
    print(fixed)