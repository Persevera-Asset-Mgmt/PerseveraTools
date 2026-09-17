# PerseveraTools

Internal tools package for Persevera Asset Management.

## Installation

Install directly from GitHub:

```
pip install git+https://github.com/Persevera-Asset-Mgmt/PerseveraTools.git
```

## Configuration

1. Create a .persevera directory in your home folder:

```
mkdir %USERPROFILE%\.persevera  # Windows
mkdir ~/.persevera              # Mac/Linux
```

1. Create a .env file in the .persevera directory with the following structure:

```
# Paths
PERSEVERA_DATA_PATH=
PERSEVERA_AUTOMATION_PATH=

# Database Configuration
PERSEVERA_DB_USER=
PERSEVERA_DB_PASSWORD=
PERSEVERA_DB_HOST=
PERSEVERA_DB_PORT=
PERSEVERA_DB_NAME=

# API Keys
PERSEVERA_FRED_API_KEY=

# Logging Configuration (Optional)
PERSEVERA_LOG_LEVEL=INFO
PERSEVERA_LOG_FILE=
PERSEVERA_LOG_DIR=
```



