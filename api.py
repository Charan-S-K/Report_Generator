import requests, base64, os, dotenv, time
dotenv.load_dotenv()

LATEX_TEMPLATE = r"""
 \documentclass[conference]{IEEEtran}

      \usepackage[utf8]{inputenc}
      \usepackage[T1]{fontenc}
      \usepackage{cite}
      \usepackage{textcomp}

      % Math and algorithms
      \usepackage{amsmath,amssymb,amsfonts}
      \usepackage{algorithmic}
      \usepackage{algorithm}

      % Figures and tables
      \usepackage{graphicx}
      \usepackage{xcolor}
      \usepackage{subcaption}
      \usepackage{booktabs}
      \usepackage{array}
      \usepackage{multirow}
      \usepackage{float}

      % Code, units, diagrams
      \usepackage{listings}
      \usepackage{siunitx}
      \usepackage{tikz}
      \usepackage{pgfplots}
      \pgfplotsset{compat=newest}

      % Links (keep last)
      \usepackage{url}
      \usepackage{hyperref}

    \title{Sample report title}

    \author{

    \IEEEauthorblockN{abc}
    \IEEEauthorblockA{asdfg}\\
    \and
    \IEEEauthorblockN{def}
    \IEEEauthorblockA{ghjjk}\\
    \and
    \IEEEauthorblockN{ghi}
    \IEEEauthorblockA{jkloi}\\

    }

      \begin{document}
      \maketitle

    \begin{abstract}
    Nam and Pardo (2011): "A smart city infuses information into its physical infrastructure to improve conveniences, facilitate mobility, add efficiencies, conserve energy, improve the quality of air and water, identify problems and fix them quickly, recover rapidly from disasters, collect data to make better decisions, deploy resources effectively, and share data to enable collaboration across entities and domains.
    \end{abstract}

    \begin{IEEEkeywords}
    This is a keywords for the above abstract
    \end{IEEEkeywords}

    \section{Smart city}
    A smart city is an urban model that leverages technology, human capital, and governance to improve sustainability, efficiency, and social inclusion, which are considered goals for cities of the future.[1] Smart cities use digital technology to collect data and operate services.[2][3] Data is collected from citizens, devices, buildings, or cameras. Smart city applications are diverse and include, but are not limited to, traffic and transportation systems,[4] power plants, utilities, urban forestry,[5] water supply networks, waste disposal, criminal investigations, information systems, schools, libraries, hospitals, and other community services.[6][7] The foundation of a smart city is built on the integration of people, technology, and processes, which connect and interact across sectors such as healthcare, transportation, education, infrastructure, etc.[8] Smart cities are characterized by the ways in which their local governments monitor, analyze, plan, and govern the city. In a smart city, data sharing extends to businesses, citizens, and other third parties who can derive benefit from using that data.[9][10] The three largest sources of spending associated with smart cities as of 2022 were visual surveillance, public transit, and outdoor lighting.[11]

    \subsection{One}
    Smart cities integrate Information and Communication Technologies (ICT), and devices connected to the Internet of things (IOT) network to optimize city services and connect to citizens.[12][13][14] ICT can enhance the quality, performance, and interactivity of urban services, reduce costs and resource consumption, and to increase contact between citizens and government.[15] Smart city applications manage urban flows and allow for real-time responses.[16] A smart city may be more prepared to respond to challenges than one with a conventional "transactional" relationship with its citizens.[17][18] Yet, the term is open to many interpretations.[19] Many cities have already adopted some sort of smart city technology.

    \subsection{Two}
    Smart city initiatives have been criticized as driven by corporations,[20][21] poorly adapted to residents' needs,[22][23] as largely unsuccessful,[citation needed] and as a move toward totalitarian surveillance.[24]

    \begin{table}[htbp]
    \caption{Matrix}
    \label{tab:matrix}
    \centering
    \begin{tabular}{|c|c|c|}
    \hline
    \textbf{a}&\textbf{b}&\textbf{c}\\ \hline
    1&2&3\\ \hline  4&5&6\\ \hline  7&8&9\\ \hline
    \end{tabular}
    \end{table}

    \begin{figure}[htbp]
    \centering
    \advGetImage{key = image1, width=5cm, type = jpg}
    \caption{My figure caption}
    \label{fig:fig1}
    \end{figure}

    \begin{thebibliography}{00}
    \bibitem{ref_paper1}
    This is a paper reference
    \bibitem{ref_book1}
    This is a book reference
    \end{thebibliography}

      \end{document}
"""

API_KEY = os.environ["ADV_API_KEY"]
URL = "https://api.advicement.io/v1/templates/pub-tex-to-pdf-with-pdflatex-v1/compile"
IMAGE_FILE = "image1.jpeg"
OUT_FILE = "out.pdf"


def compile_pdf(latex_code, img_b64):
    r = requests.post(
        URL,
        json={"texFileContent": latex_code, "image1": img_b64},
        headers={"Adv-Security-Token": API_KEY},
        timeout=120,
    )
    print("POST:", r.status_code)
    if r.status_code != 200:
        print(r.text[:1000])
        return False

    status_url = r.json()["documentStatusUrl"]
    data = {}
    for _ in range(40):                       # waits up to ~80 seconds
        s = requests.get(status_url, timeout=30)
        if s.status_code == 200:
            try:
                data = s.json()
            except ValueError:
                data = {}
            if "documentUrl" in data:
                pdf = requests.get(data["documentUrl"], timeout=60)
                with open(OUT_FILE, "wb") as f:
                    f.write(pdf.content)
                print("saved", os.path.abspath(OUT_FILE))
                return True
            code = data.get("statusCode")
            if isinstance(code, int) and code >= 400:
                print(code, data.get("statusDescription"))
                for key in ("latexLogUrl", "calculationLogUrl"):
                    if key in data:
                        print(f"--- {key} (last 3000 chars) ---")
                        print(requests.get(data[key], timeout=30).text[-3000:])
                return False
        time.sleep(2)
    print("Timed out waiting for the document")
    return False


if __name__ == "__main__":
    if os.path.exists(OUT_FILE):
        os.remove(OUT_FILE)                   # avoid mistaking an old PDF for a new one

    with open(IMAGE_FILE, "rb") as f:
        img_b64 = base64.b64encode(f.read()).decode()

    for img_type in ("jpg", "jpeg"):
        print(f"\n=== trying type = {img_type} ===")
        if compile_pdf(LATEX_TEMPLATE.replace("<<IMGTYPE>>", img_type), img_b64):
            break