.PHONY: help build run bootstrap deploy deploy-swarm deploy-compose clean install test logs

help:
	@echo "HiveMind - GitOps for Docker Swarm"
	@echo ""
	@echo "Available targets:"
	@echo "  build          - Build Docker image"
	@echo "  run            - Run HiveMind locally (requires Python)"
	@echo "  bootstrap      - Run bootstrap process"
	@echo "  deploy-compose - Deploy with Docker Compose"
	@echo "  deploy-swarm   - Deploy to Docker Swarm"
	@echo "  logs           - View HiveMind logs"
	@echo "  clean          - Clean up temporary files"
	@echo "  install        - Install Python dependencies"
	@echo "  test           - Run tests (when available)"

build:
	docker build -t hivemind:latest .

run:
	python3 -m src.main hivemind-config.yml

bootstrap:
	./bootstrap.sh

deploy-compose: build
	docker-compose up -d

deploy-swarm: build
	docker stack deploy -c hivemind-stack.yml hivemind

deploy: deploy-swarm

logs:
	@if docker stack ps hivemind 2>/dev/null | grep -q hivemind; then \
		docker service logs -f hivemind_controller; \
	else \
		docker-compose logs -f hivemind; \
	fi

clean:
	find . -type d -name __pycache__ -exec rm -rf {} +
	find . -type f -name "*.pyc" -delete
	find . -type f -name "*.log" -delete
	docker-compose down 2>/dev/null || true

install:
	pip3 install -r requirements.txt

test:
	@echo "Tests not yet implemented"
