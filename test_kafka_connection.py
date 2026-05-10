#!/usr/bin/env python3
"""Test Kafka connectivity from host machine."""

import socket
import sys


def test_port_connectivity(host, port, timeout=5):
    """Test if a port is reachable."""
    try:
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.settimeout(timeout)
        result = sock.connect_ex((host, port))
        sock.close()
        return result == 0
    except Exception as e:
        print(f"Socket error: {e}")
        return False


def test_kafka_connection():
    """Test Kafka connectivity."""
    print("=== Kafka Connectivity Test ===\n")

    # Test 1: Port connectivity
    print("1. Testing port connectivity...")
    if test_port_connectivity("localhost", 9092):
        print("   ✓ Port 9092 is reachable on localhost")
    else:
        print("   ✗ Port 9092 is NOT reachable on localhost")
        return False

    # Test 2: Try to import confluent-kafka
    print("\n2. Testing Kafka client library...")
    try:
        from confluent_kafka import Consumer, Producer, admin

        print("   ✓ confluent-kafka library is installed")
    except ImportError as e:
        print(f"   ✗ confluent-kafka library not available: {e}")
        return False

    # Test 3: Test actual Kafka connection
    print("\n3. Testing Kafka broker connection...")
    try:
        from confluent_kafka.admin import AdminClient

        admin_client = AdminClient(
            {
                "bootstrap.servers": "localhost:9092",
                "socket.timeout.ms": 5000,
                "api.version.request": "true",
            }
        )

        # Try to get metadata
        metadata = admin_client.list_topics(timeout=5)
        print(f"   ✓ Connected to Kafka broker (ID: {metadata.orig_broker_id})")
        print(f"   ✓ Found {len(metadata.topics)} topics")
        print("\n   Available topics:")
        for topic in sorted(metadata.topics.keys())[:10]:
            print(f"      - {topic}")

        return True

    except Exception as e:
        print(f"   ✗ Kafka connection failed: {e}")
        return False


if __name__ == "__main__":
    success = test_kafka_connection()
    sys.exit(0 if success else 1)
