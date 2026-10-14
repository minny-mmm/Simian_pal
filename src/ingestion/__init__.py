from src.ingestion.cops_reader import CopsReader
from src.ingestion.nhanes_reader import NhanesReader

READERS = {"cops": CopsReader, "nhanes": NhanesReader}
