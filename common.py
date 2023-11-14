import logging

logging.basicConfig(filename="log.txt",
                    filemode='w',
                    format='[%(asctime)s][%(levelname)s][%(name)s] %(message)s',
                    level=logging.DEBUG)