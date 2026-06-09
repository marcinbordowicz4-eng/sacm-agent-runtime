class DiffParser:
    def summarize(self, diff_text: str) -> dict:
        files = [line[4:] for line in diff_text.splitlines() if line.startswith("+++")]
        return {"files": files, "line_count": len(diff_text.splitlines())}
