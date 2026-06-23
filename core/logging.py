import logging
import json
import time

class StructuredSampledLogger(logging.Logger):
    """
    Structured, sampled logging for SyncForge Enterprise.
    Suppresses repetitive logs and outputs in JSON format.
    """
    _last_logs = {}
    _sample_rate = 0.1 # 10% sampling for noisy logs

    def _should_log(self, msg: str, level: int) -> bool:
        if level >= logging.WARNING:
            return True
        now = time.time()
        last = self._last_logs.get(msg, 0)
        if now - last < 5.0: # Suppress identical messages within 5 seconds
            return False
        self._last_logs[msg] = now
        return True

    def _structured_log(self, level, msg, args, exc_info=None, extra=None, stack_info=False):
        if not self._should_log(msg, level):
            return
            
        record = {
            "timestamp": time.time(),
            "level": logging.getLevelName(level),
            "message": msg % args if args else msg,
        }
        if extra:
            record.update(extra)
            
        # Write structured JSON log
        super().log(level, json.dumps(record), (), exc_info=exc_info, extra=None, stack_info=stack_info)

    def info(self, msg, *args, **kwargs):
        self._structured_log(logging.INFO, msg, args, **kwargs)
        
    def debug(self, msg, *args, **kwargs):
        self._structured_log(logging.DEBUG, msg, args, **kwargs)
        
    def warning(self, msg, *args, **kwargs):
        self._structured_log(logging.WARNING, msg, args, **kwargs)

    def error(self, msg, *args, **kwargs):
        self._structured_log(logging.ERROR, msg, args, **kwargs)

logging.setLoggerClass(StructuredSampledLogger)
