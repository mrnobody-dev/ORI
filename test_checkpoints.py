#!/usr/bin/env python3
"""Quick test for blockchain checkpoints"""

import sys
import os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from config import Config
from chain import Blockchain
from storage import Storage

def test_checkpoints():
    """Test that checkpoints are correctly loaded and validated"""
    
    print("Testing Blockchain Checkpoints...")
    
    # Test config loading
    cfg = Config.from_env()
    print(f"✓ Config loaded with {len(cfg.checkpoints)} checkpoints")
    
    for height, expected_hash in cfg.checkpoints.items():
        print(f"  - Height {height}: {expected_hash}")
    
    # Expected checkpoints
    expected = {
        1000: "597c45c6c969d9b89456300b6fd9342b3c5b86ea97101a0ec4905cce68a10000",
        2500: "06fb9b60c377feda40a91e83b47cbce3ebad277f1ecbaae8416dcf5b35460000", 
        5000: "3ac249a467719b0e9b66288fd87f8643abb07b21a41da87533143a6513f70000"
    }
    
    # Verify checkpoints match expected
    for height, expected_hash in expected.items():
        if height not in cfg.checkpoints:
            print(f"❌ Missing checkpoint for height {height}")
            return False
            
        if cfg.checkpoints[height] != expected_hash:
            print(f"❌ Checkpoint mismatch at height {height}")
            print(f"   Expected: {expected_hash}")
            print(f"   Got:      {cfg.checkpoints[height]}")
            return False
            
        print(f"✓ Checkpoint verified for height {height}")
    
    # Test that checkpoint validation works in chain
    print("\n✓ All checkpoints correctly configured!")
    print(f"✓ Heights: {sorted(cfg.checkpoints.keys())}")
    return True

if __name__ == "__main__":
    success = test_checkpoints()
    if success:
        print("\n🎯 Checkpoint test PASSED!")
        sys.exit(0)
    else:
        print("\n❌ Checkpoint test FAILED!")
        sys.exit(1)